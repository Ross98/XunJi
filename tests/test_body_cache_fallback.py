"""Offline queries may use compatible complete snapshots without claiming freshness."""

import asyncio
from copy import deepcopy

import pytest

from data_service import DataService, extract_body_records

START, END = '2026-09-24', '2026-10-23'
FULL_KEY = f'body:all:None:{END}:500'
WINDOW_KEY = f'body:all:{START}:{END}:500'
ROWS = [
    {'datestr': '2026-09-23', 'type': 'weight', 'value': 69},
    {'datestr': START, 'type': 'weight', 'value': 70},
    {'datestr': END, 'type': 'weight', 'value': 71},
    {'datestr': END, 'type': 'bodyfat', 'value': 20},
    {'datestr': '2026-10-24', 'type': 'weight', 'value': 99},
]


class Client:
    def __init__(self, result=None):
        self.result = result
        self.calls = 0

    async def query_body(self, *args, **kwargs):
        self.calls += 1
        if self.result is None:
            raise RuntimeError('proxy unavailable')
        return self.result


def seed(cache, key=FULL_KEY, rows=ROWS, **extra):
    data = {'records': deepcopy(rows), **extra}
    cache.set(key, data)
    return data


@pytest.mark.parametrize('failure', ['transport', 'invalid_response'])
def test_failed_window_query_uses_full_snapshot_without_cache_or_marker_write(tmp_cache, failure):
    original = seed(tmp_cache)
    marker = {'time': '2026-10-01T00:00:00+00:00'}
    tmp_cache.set('_xunji_refresh_time', marker)
    client = Client({'success': False} if failure == 'invalid_response' else None)
    service = DataService(tmp_cache, client)
    result = asyncio.run(service.get_body(START, END))
    assert extract_body_records(result) == ROWS[1:4]
    assert result['_cache_fallback']
    assert not service.body_history_complete(result)
    assert tmp_cache.get(WINDOW_KEY) is None
    assert tmp_cache.get(FULL_KEY) == original
    assert tmp_cache.get('_xunji_refresh_time') == marker
    assert client.calls == 1


def test_fallback_filters_requested_types_and_allows_different_page_size(tmp_cache):
    seed(tmp_cache, key=f'body:all:None:{END}:2', _history_complete=True)
    result = asyncio.run(DataService(tmp_cache, Client()).get_body(START, END, types=['weight']))
    assert extract_body_records(result) == ROWS[1:3]


@pytest.mark.parametrize('key,extra', [
    (f'body:all:None:{END}:2', {}),  # A full page is not a complete snapshot.
    (f'body:weight:None:{END}:500', {}),  # Missing requested metric types.
    (f'body:all:2026-10-01:{END}:500', {}),  # Does not cover requested lower bound.
    ('body:all:None:bad:500', {}),
])
def test_incompatible_or_truncated_snapshot_is_not_used(tmp_cache, key, extra):
    seed(tmp_cache, key=key, **extra)
    with pytest.raises(RuntimeError, match='proxy'):
        asyncio.run(DataService(tmp_cache, Client()).get_body(START, END))


def test_newest_compatible_empty_snapshot_does_not_resurrect_old_records(tmp_cache):
    seed(tmp_cache)
    newer = f'body:all:None:2026-10-22:500'
    seed(tmp_cache, key=newer, rows=[])
    with tmp_cache._conn() as conn:
        conn.execute('UPDATE cache SET mtime=? WHERE key=?', ('2026-10-22T00:00:00+00:00', FULL_KEY))
        conn.execute('UPDATE cache SET mtime=? WHERE key=?', ('2026-10-23T00:00:00+00:00', newer))
    result = asyncio.run(DataService(tmp_cache, Client()).get_body(START, END))
    assert extract_body_records(result) == []
    assert result['_cache_fallback']


def test_cache_clear_during_failure_prevents_snapshot_fallback(tmp_cache):
    seed(tmp_cache)

    class ClearingClient(Client):
        async def query_body(self, *args, **kwargs):
            tmp_cache.clear(preserve_system=True)
            raise RuntimeError('proxy unavailable')

    with pytest.raises(RuntimeError, match='proxy'):
        asyncio.run(DataService(tmp_cache, ClearingClient()).get_body(START, END))
    assert tmp_cache.get(FULL_KEY) is None
    assert tmp_cache.get(WINDOW_KEY) is None


def test_fallback_does_not_block_later_successful_refresh(tmp_cache):
    seed(tmp_cache)
    client = Client()
    service = DataService(tmp_cache, client)
    fallback = asyncio.run(service.get_body(START, END))
    assert fallback['_cache_fallback']
    fresh = {'records': [{'datestr': END, 'type': 'weight', 'value': 72}]}
    client.result = fresh
    assert asyncio.run(service.get_body(START, END)) == fresh
    assert tmp_cache.get(WINDOW_KEY) == fresh
    assert tmp_cache.get('_xunji_refresh_time')
    assert client.calls == 2


def test_older_snapshot_cannot_supply_records_outside_its_own_coverage(tmp_cache):
    seed(tmp_cache, key='body:all:None:2026-10-22:500')
    result = asyncio.run(DataService(tmp_cache, Client()).get_body(START, END))
    assert extract_body_records(result) == [ROWS[1]]


@pytest.mark.parametrize('have_snapshot', [False, True])
def test_offline_body_http_explains_cache_or_apple_health_fallback(
    isolated_health_db, tmp_cache, monkeypatch, tmp_path, have_snapshot
):
    import httpx
    from datetime import date, timedelta
    from fastapi import FastAPI
    from routes import body

    end = date.today().isoformat()
    start = (date.today() - timedelta(days=29)).isoformat()
    if have_snapshot:
        seed(tmp_cache, key=f'body:all:None:{end}:500',
             rows=[{'datestr': end, 'type': 'weight', 'value': 72}])
    service = DataService(tmp_cache, Client())
    monkeypatch.setattr(body, 'get_data_service', lambda: service)
    monkeypatch.setattr(body, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(body, 'BODY_CONFIG_PATH', str(tmp_path / 'body.json'))
    app = FastAPI()
    app.include_router(body.router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
            response = await client.get('/body?range=30')
            assert response.status_code == 200
            assert 'role="status"' in response.text
            assert '训记暂时无法读取' in response.text
            assert ('当前展示本机缓存与 Apple Health 记录' if have_snapshot else '当前仅展示 Apple Health 记录') in response.text
            assert '稍后刷新重试' in response.text
        assert tmp_cache.get(f'body:all:{start}:{end}:500') is None
        assert tmp_cache.get('_xunji_refresh_time') is None

    asyncio.run(run())


def test_partial_page_failure_can_use_snapshot_without_saving_partial_history(tmp_cache, monkeypatch):
    import data_service

    seed(tmp_cache)
    async def no_delay(seconds):
        pass
    monkeypatch.setattr(data_service, '_body_page_sleep', no_delay)

    class PagedClient(Client):
        async def query_body(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {'records': ROWS[1:3]}
            raise RuntimeError('second page unavailable')

    client = PagedClient()
    result = asyncio.run(DataService(tmp_cache, client).get_body(START, END, limit=2))
    assert extract_body_records(result) == ROWS[1:4]
    assert result['_cache_fallback']
    assert client.calls == 2
    assert tmp_cache.get(f'body:all:{START}:{END}:2') is None
    assert tmp_cache.get('_xunji_refresh_time') is None


def test_cancellation_never_turns_into_snapshot_fallback(tmp_cache):
    seed(tmp_cache)

    async def run():
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        class WaitingClient(Client):
            async def query_body(self, *args, **kwargs):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        service = DataService(tmp_cache, WaitingClient())
        reader = asyncio.create_task(service.get_body(START, END))
        await entered.wait()
        reader.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reader
        assert cancelled.is_set()
        assert tmp_cache.get(WINDOW_KEY) is None
        assert tmp_cache.get('_xunji_refresh_time') is None

    asyncio.run(asyncio.wait_for(run(), 2))
