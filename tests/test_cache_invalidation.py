import asyncio

import pytest

from cache import Cache
from data_service import DataService

START, END = '2026-10-01', '2026-10-23'
KEY = f'body:all:{START}:{END}:500'
OLD = {'records': [{'datestr': END, 'type': 'weight', 'value': 69}]}
NEW = {'records': [{'datestr': END, 'type': 'weight', 'value': 70}]}
MARKER = {'time': '2026-10-01T00:00:00+00:00'}


def test_clear_invalidates_writes_across_cache_instances(tmp_path):
    first = Cache(str(tmp_path / 'cache.sqlite'))
    second = Cache(str(tmp_path / 'cache.sqlite'))
    generation = first.get_generation()
    second.clear(preserve_system=True)
    assert not first.set_many_if_generation({KEY: OLD, '_xunji_refresh_time': MARKER}, generation)
    assert first.get(KEY) is None
    assert first.get('_xunji_refresh_time') is None
    current = first.get_generation()
    assert current != generation
    assert first.set_many_if_generation({KEY: NEW, '_xunji_refresh_time': MARKER}, current)
    assert second.get(KEY) == NEW
    assert second.get('_xunji_refresh_time') == MARKER


def test_late_training_response_cannot_repopulate_cleared_cache(tmp_cache):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        class Client:
            async def fetch_training(self, day, full):
                entered.set()
                await release.wait()
                return {'trains': [{'datestr': END}]}

        tmp_cache.set('_xunji_refresh_time', MARKER)
        service = DataService(tmp_cache, Client())
        pending = asyncio.create_task(service.get_training(END))
        await entered.wait()
        tmp_cache.clear(preserve_system=True)
        release.set()
        await pending
        assert tmp_cache.get(f'training:{END}:False') is None
        assert tmp_cache.get('_xunji_refresh_time') == MARKER

    asyncio.run(asyncio.wait_for(run(), timeout=2))


def test_new_body_read_after_clear_never_joins_or_gets_overwritten_by_old_read(tmp_cache):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        class Client:
            calls = 0

            async def query_body(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    entered.set()
                    await release.wait()
                    return OLD
                return NEW

        client = Client()
        service = DataService(tmp_cache, client)
        old = asyncio.create_task(service.get_body(START, END))
        await entered.wait()
        tmp_cache.clear(preserve_system=True)
        try:
            assert await asyncio.wait_for(service.get_body(START, END), timeout=.5) == NEW
        finally:
            release.set()
            await old
        assert client.calls == 2
        assert tmp_cache.get(KEY) == NEW
        assert not service._body_requests

    asyncio.run(asyncio.wait_for(run(), timeout=2))


def test_refresh_payload_and_marker_roll_back_together_on_write_failure(tmp_cache):
    import sqlite3
    import pytest
    tmp_cache.set(KEY, OLD)
    tmp_cache.set('_xunji_refresh_time', MARKER)
    with tmp_cache._conn() as conn:
        conn.execute("""CREATE TRIGGER reject_marker BEFORE INSERT ON cache
            WHEN NEW.key = '_xunji_refresh_time'
            BEGIN SELECT RAISE(ABORT, 'simulated marker write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        tmp_cache.set_many_if_generation(
            {KEY: NEW, '_xunji_refresh_time': {'time': '2026-10-23'}}, tmp_cache.get_generation())
    assert tmp_cache.get(KEY) == OLD
    assert tmp_cache.get('_xunji_refresh_time') == MARKER


def test_old_training_response_cannot_overwrite_new_generation(tmp_cache):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        old = {'trains': [{'datestr': END, 'title': 'old'}]}
        new = {'trains': [{'datestr': END, 'title': 'new'}]}

        class Client:
            calls = 0

            async def fetch_training(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    entered.set()
                    await release.wait()
                    return old
                return new

        service = DataService(tmp_cache, Client())
        pending = asyncio.create_task(service.get_training(END))
        await entered.wait()
        tmp_cache.clear(preserve_system=True)
        assert await service.get_training(END) == new
        release.set()
        await pending
        assert tmp_cache.get(f'training:{END}:False') == new

    asyncio.run(asyncio.wait_for(run(), timeout=2))


def test_conditional_delete_preserves_new_payload_in_same_generation(tmp_path):
    first = Cache(str(tmp_path / 'cache.sqlite'))
    writer = Cache(str(tmp_path / 'cache.sqlite'))
    broken = {'records': 'bad'}
    first.set(KEY, broken)
    generation = first.get_generation()
    writer.set(KEY, NEW)
    assert not first.delete_if_unchanged(KEY, broken, generation)
    assert writer.get(KEY) == NEW


def test_conditional_delete_cannot_cross_cache_clear(tmp_path):
    first = Cache(str(tmp_path / 'cache.sqlite'))
    writer = Cache(str(tmp_path / 'cache.sqlite'))
    broken = {'records': 'bad'}
    first.set(KEY, broken)
    generation = first.get_generation()
    writer.clear(preserve_system=True)
    writer.set(KEY, broken)
    assert not first.delete_if_unchanged(KEY, broken, generation)
    assert writer.get(KEY) == broken
    assert first.delete_if_unchanged(KEY, broken, writer.get_generation())
    assert writer.get(KEY) is None
    assert not first.delete_if_unchanged(KEY, broken, writer.get_generation())


@pytest.mark.parametrize("outcome", ["failure", "success"])
def test_paused_invalid_body_reader_cannot_delete_post_clear_result(tmp_cache, monkeypatch, outcome):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        tmp_cache.set(KEY, {'records': 'bad'})

        class Client:
            async def query_body(self, *args, **kwargs):
                if outcome == 'failure':
                    raise RuntimeError('old query failed')
                return OLD

        service = DataService(tmp_cache, Client())
        original = service._load_body_history

        async def paused(*args):
            entered.set()
            await release.wait()
            return await original(*args)

        monkeypatch.setattr(service, '_load_body_history', paused)
        reader = asyncio.create_task(service.get_body(START, END))
        await entered.wait()
        tmp_cache.clear(preserve_system=True)
        tmp_cache.set(KEY, NEW)
        tmp_cache.set('_xunji_refresh_time', MARKER)
        release.set()
        results = await asyncio.gather(reader, return_exceptions=True)
        if outcome == 'failure':
            assert isinstance(results[0], RuntimeError)
        else:
            assert results == [OLD]
        assert tmp_cache.get(KEY) == NEW
        assert tmp_cache.get('_xunji_refresh_time') == MARKER

    asyncio.run(asyncio.wait_for(run(), 2))


@pytest.mark.parametrize('clear', [False, True])
def test_training_cleanup_preserves_snapshot_replaced_after_validation_read(tmp_cache, monkeypatch, clear):
    key = f'training:{END}:False'
    broken = {'res': {'trains': None}}
    fresh = {'trains': [{'datestr': END, 'title': 'new snapshot'}]}
    tmp_cache.set(key, broken)
    writer = Cache(tmp_cache._db_path)
    original_get = tmp_cache.get
    armed = False

    def racing_get(cache_key):
        nonlocal armed
        snapshot = original_get(cache_key)
        if armed and cache_key == key:
            armed = False
            if clear:
                writer.clear(preserve_system=True)
            writer.set(key, fresh)
            writer.set('_xunji_refresh_time', MARKER)
        return snapshot

    class Client:
        async def fetch_training(self, *args):
            raise RuntimeError('old query failed')

    service = DataService(tmp_cache, Client())
    original_load = service._load_training

    async def load(*args):
        nonlocal armed
        armed = True
        return await original_load(*args)

    monkeypatch.setattr(tmp_cache, 'get', racing_get)
    monkeypatch.setattr(service, '_load_training', load)
    with pytest.raises(RuntimeError, match='old query failed'):
        asyncio.run(service.get_training(END))
    assert writer.get(key) == fresh
    assert writer.get('_xunji_refresh_time') == MARKER
