"""Concurrent training readers share refreshes and obey service shutdown."""

import asyncio

import pytest

from data_service import DataService

DAY = '2026-08-03'
KEY = f'training:{DAY}:False'
OLD = {'trains': [{'datestr': DAY, 'title': 'cached', 'movements': []}]}
FRESH = {'trains': [{'datestr': DAY, 'title': 'fresh', 'movements': []}]}


class GatedClient:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.active = 0
        self.cancelled = False
        self.closed_with_active = None
        self.fail_first = False

    async def fetch_training(self, *args):
        self.calls += 1
        attempt = self.calls
        self.active += 1
        self.entered.set()
        try:
            await self.release.wait()
            if self.fail_first and attempt == 1:
                raise RuntimeError('temporary upstream failure')
            return FRESH
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.active -= 1

    async def close(self):
        self.closed_with_active = self.active


@pytest.mark.parametrize('force', [False, True])
def test_training_readers_share_refresh_and_result(tmp_cache, force):
    async def run():
        if force:
            tmp_cache.set(KEY, OLD)
        client = GatedClient()
        service = DataService(tmp_cache, client)
        first = asyncio.create_task(service.get_training(DAY, force_refresh=force))
        await client.entered.wait()
        second = asyncio.create_task(service.get_training(DAY, force_refresh=force))
        await asyncio.sleep(0)
        client.release.set()
        assert await asyncio.gather(first, second) == [FRESH, FRESH]
        assert client.calls == 1
        assert tmp_cache.get(KEY) == FRESH

    asyncio.run(asyncio.wait_for(run(), 2))


def test_cancel_one_training_reader_keeps_shared_refresh_alive(tmp_cache):
    async def run():
        client = GatedClient()
        service = DataService(tmp_cache, client)
        first = asyncio.create_task(service.get_training(DAY))
        await client.entered.wait()
        second = asyncio.create_task(service.get_training(DAY))
        await asyncio.sleep(0)
        first.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await first
            assert not client.cancelled
        finally:
            client.release.set()
            result = await second
        assert result == FRESH
        assert client.calls == 1

    asyncio.run(asyncio.wait_for(run(), 2))


def test_cancel_last_training_reader_stops_work_without_cache_write(tmp_cache):
    async def run():
        client = GatedClient()
        service = DataService(tmp_cache, client)
        reader = asyncio.create_task(service.get_training(DAY))
        await client.entered.wait()
        reader.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reader
        assert client.cancelled and client.active == 0
        assert tmp_cache.get(KEY) is None
        assert tmp_cache.get('_xunji_refresh_time') is None
        client.release.set()
        assert await service.get_training(DAY) == FRESH
        assert client.calls == 2

    asyncio.run(asyncio.wait_for(run(), 2))


def test_training_close_joins_work_before_closing_client(tmp_cache):
    async def run():
        client = GatedClient()
        service = DataService(tmp_cache, client)
        reader = asyncio.create_task(service.get_training(DAY))
        await client.entered.wait()
        try:
            await service.close()
            assert client.cancelled
            assert client.closed_with_active == 0
            with pytest.raises(asyncio.CancelledError):
                await reader
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        assert tmp_cache.get(KEY) is None
        tmp_cache.set(KEY, OLD)
        with pytest.raises(RuntimeError, match='closed'):
            await service.get_training(DAY)

    asyncio.run(asyncio.wait_for(run(), 2))


def test_failed_shared_training_read_can_retry_without_cache_or_marker(tmp_cache):
    async def run():
        client = GatedClient()
        client.fail_first = True
        service = DataService(tmp_cache, client)
        first = asyncio.create_task(service.get_training(DAY))
        await client.entered.wait()
        second = asyncio.create_task(service.get_training(DAY))
        await asyncio.sleep(0)
        client.release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        assert all(isinstance(result, RuntimeError) for result in results)
        assert client.calls == 1
        assert tmp_cache.get(KEY) is None
        assert tmp_cache.get('_xunji_refresh_time') is None
        assert await service.get_training(DAY) == FRESH
        assert client.calls == 2

    asyncio.run(asyncio.wait_for(run(), 2))


def test_training_cache_clear_separates_shared_generations(tmp_cache):
    async def run():
        class Client:
            def __init__(self):
                self.gates = [asyncio.Event(), asyncio.Event()]
                self.entered = [asyncio.Event(), asyncio.Event()]
                self.calls = 0

            async def fetch_training(self, *args):
                index = self.calls
                self.calls += 1
                self.entered[index].set()
                await self.gates[index].wait()
                return [OLD, FRESH][index]

        client = Client()
        service = DataService(tmp_cache, client)
        first = asyncio.create_task(service.get_training(DAY))
        await client.entered[0].wait()
        peer = asyncio.create_task(service.get_training(DAY))
        await asyncio.sleep(0)
        tmp_cache.clear(preserve_system=True)
        newer = asyncio.create_task(service.get_training(DAY))
        await client.entered[1].wait()
        client.gates[1].set()
        assert await newer == FRESH
        marker = tmp_cache.get('_xunji_refresh_time')
        client.gates[0].set()
        assert await asyncio.gather(first, peer) == [OLD, OLD]
        assert client.calls == 2
        assert tmp_cache.get(KEY) == FRESH
        assert tmp_cache.get('_xunji_refresh_time') == marker

    asyncio.run(asyncio.wait_for(run(), 2))


def test_training_full_and_summary_requests_stay_independent(tmp_cache):
    async def run():
        client = GatedClient()
        service = DataService(tmp_cache, client)
        summary = asyncio.create_task(service.get_training(DAY))
        await client.entered.wait()
        full = asyncio.create_task(service.get_training(DAY, full=True))
        await asyncio.sleep(0)
        client.release.set()
        assert await asyncio.gather(summary, full) == [FRESH, FRESH]
        assert client.calls == 2
        assert tmp_cache.get(KEY) == FRESH
        assert tmp_cache.get(f'training:{DAY}:True') == FRESH

    asyncio.run(asyncio.wait_for(run(), 2))


def test_service_close_joins_both_training_and_body_requests(tmp_cache):
    async def run():
        class Client(GatedClient):
            async def query_body(self, *args, **kwargs):
                return await self.fetch_training()

        client = Client()
        service = DataService(tmp_cache, client)
        training = asyncio.create_task(service.get_training(DAY))
        await client.entered.wait()
        client.entered.clear()
        body = asyncio.create_task(service.get_body('2026-08-01', DAY))
        await client.entered.wait()
        await service.close()
        results = await asyncio.gather(training, body, return_exceptions=True)
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        assert client.closed_with_active == 0
        assert tmp_cache.get(KEY) is None

    asyncio.run(asyncio.wait_for(run(), 2))


@pytest.mark.parametrize('failure', ['transport', 'invalid_json_shape'])
def test_shared_training_failure_preserves_old_cache_and_refresh_marker(tmp_cache, failure):
    async def run():
        class Client(GatedClient):
            async def fetch_training(self, *args):
                self.calls += 1
                self.entered.set()
                await self.release.wait()
                if failure == 'transport':
                    raise RuntimeError('transport failed')
                return {'success': False, 'res': 'bad response'}

        tmp_cache.set(KEY, OLD)
        marker = {'time': '2026-08-01T00:00:00+00:00'}
        tmp_cache.set('_xunji_refresh_time', marker)
        client = Client()
        service = DataService(tmp_cache, client)
        first = asyncio.create_task(service.get_training(DAY, force_refresh=True))
        await client.entered.wait()
        peer = asyncio.create_task(service.get_training(DAY, force_refresh=True))
        await asyncio.sleep(0)
        client.release.set()
        assert await asyncio.gather(first, peer) == [OLD, OLD]
        assert client.calls == 1
        assert tmp_cache.get(KEY) == OLD
        assert tmp_cache.get('_xunji_refresh_time') == marker

    asyncio.run(asyncio.wait_for(run(), 2))


def test_concurrent_training_detail_http_requests_share_one_upstream_read(tmp_cache, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from routes import training

    async def run():
        client = GatedClient()
        service = DataService(tmp_cache, client)
        monkeypatch.setattr(training, 'get_data_service', lambda: service)
        monkeypatch.setattr(training, 'get_cache', lambda: tmp_cache)
        app = FastAPI()
        app.include_router(training.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as browser:
            readers = [asyncio.create_task(browser.get('/training/' + DAY)) for _ in range(12)]
            await client.entered.wait()
            await asyncio.sleep(0)
            client.release.set()
            responses = await asyncio.gather(*readers)
            assert all(response.status_code == 200 and 'fresh' in response.text for response in responses)
        assert client.calls == 1
        assert tmp_cache.get(f'training:{DAY}:True') == FRESH
        await service.close()
        assert client.closed_with_active == 0

    asyncio.run(asyncio.wait_for(run(), 2))
