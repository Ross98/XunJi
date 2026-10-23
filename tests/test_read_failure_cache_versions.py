"""An upstream failure cannot revive a removed or superseded cache snapshot."""
import asyncio

import pytest

from data_service import DataService

DAY = '2026-10-23'
START = '2026-09-24'
ERROR = {'success': False, 'res': 'synthetic unavailable'}


def payload(kind, label):
    if kind == 'training':
        return {'trains': [{'datestr': DAY, 'title': label}]}
    return {'records': [{'datestr': DAY, 'type': 'weight', 'value': 70 if label == 'old' else 72}]}


def key(kind):
    return f'training:{DAY}:False' if kind == 'training' else f'body:all:{START}:{DAY}:500'


@pytest.mark.parametrize('kind', ['training', 'body'])
@pytest.mark.parametrize('failure', ['transport', 'invalid'])
@pytest.mark.parametrize('mutation', ['clear', 'delete', 'replace', 'empty'])
def test_failure_uses_current_snapshot_and_never_revives_old(kind, failure, mutation, tmp_cache):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        old = payload(kind, 'old')
        fresh = payload(kind, 'fresh') if mutation == 'replace' else {'trains': []} if kind == 'training' else {'records': []}
        tmp_cache.set(key(kind), old)
        marker = {'time': '2026-10-22T00:00:00+00:00'}
        tmp_cache.set('_xunji_refresh_time', marker)

        class Client:
            calls = 0

            async def read(self, *args, **kwargs):
                self.calls += 1
                entered.set()
                await release.wait()
                if failure == 'transport':
                    raise RuntimeError('synthetic upstream failure')
                return ERROR
            fetch_training = read
            query_body = read

        client = Client()
        service = DataService(tmp_cache, client)

        def read():
            if kind == 'training':
                return service.get_training(DAY, force_refresh=True)
            return service.get_body(START, DAY, force_refresh=True)

        first = asyncio.create_task(read())
        await entered.wait()
        peer = asyncio.create_task(read())
        await asyncio.sleep(0)
        if mutation == 'clear':
            tmp_cache.clear(preserve_system=True)
        elif mutation == 'delete':
            tmp_cache.delete(key(kind))
        else:
            tmp_cache.set(key(kind), fresh)
        release.set()
        results = await asyncio.gather(first, peer, return_exceptions=True)
        if mutation in ('clear', 'delete'):
            if failure == 'transport':
                assert all(isinstance(result, RuntimeError) for result in results)
            else:
                assert results == [ERROR, ERROR]
            assert tmp_cache.get(key(kind)) is None
        else:
            assert results == [fresh, fresh]
            assert tmp_cache.get(key(kind)) == fresh
        assert client.calls == 1
        assert tmp_cache.get('_xunji_refresh_time') == marker
        assert not service._training_requests and not service._body_requests
    asyncio.run(asyncio.wait_for(run(), 2))


@pytest.mark.parametrize('kind', ['training', 'body'])
@pytest.mark.parametrize('failure', ['transport', 'invalid'])
def test_clear_during_fallback_read_cannot_leak_returned_snapshot(kind, failure, tmp_cache, monkeypatch):
    import data_service
    tmp_cache.set(key(kind), payload(kind, 'old'))
    function = 'get_cached_training' if kind == 'training' else 'get_cached_body'
    original = getattr(data_service, function)
    armed = False

    def racing_read(*args):
        nonlocal armed
        result = original(*args)
        if armed:
            armed = False
            tmp_cache.clear(preserve_system=True)
        return result
    monkeypatch.setattr(data_service, function, racing_read)

    class Client:
        async def read(self, *args, **kwargs):
            nonlocal armed
            armed = True
            if failure == 'transport':
                raise RuntimeError('synthetic upstream failure')
            return ERROR
        fetch_training = read
        query_body = read

    service = DataService(tmp_cache, Client())
    async def read():
        if kind == 'training':
            return await service.get_training(DAY, force_refresh=True)
        return await service.get_body(START, DAY, force_refresh=True)
    if failure == 'transport':
        with pytest.raises(RuntimeError, match='synthetic upstream'):
            asyncio.run(read())
    else:
        assert asyncio.run(read()) == ERROR
    assert tmp_cache.get(key(kind)) is None


@pytest.mark.parametrize('mutation', ['clear', 'delete'])
def test_training_cooldown_does_not_reuse_snapshot_removed_before_worker_runs(tmp_cache, monkeypatch, mutation):
    old, fresh = payload('training', 'old'), payload('training', 'fresh')
    tmp_cache.set(key('training'), old)

    class Client:
        calls = 0

        async def fetch_training(self, *args):
            self.calls += 1
            return ERROR if self.calls == 1 else fresh
    client = Client()
    service = DataService(tmp_cache, client)
    assert asyncio.run(service.get_training(DAY, force_refresh=True)) == old
    original = service._load_training

    async def racing_load(*args):
        if mutation == 'clear':
            tmp_cache.clear(preserve_system=True)
        else:
            tmp_cache.delete(key('training'))
        return await original(*args)
    monkeypatch.setattr(service, '_load_training', racing_load)
    assert asyncio.run(service.get_training(DAY, force_refresh=True)) == fresh
    assert client.calls == 2
