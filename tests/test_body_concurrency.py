import asyncio

import pytest

from data_service import DataService

START, END = '2026-10-01', '2026-10-23'
KEY = f'body:all:{START}:{END}:500'
FRESH = {'records': [{'datestr': END, 'type': 'weight', 'value': 70}]}


class GatedClient:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.cancelled = False
        self.closed = False

    async def query_body(self, *args, **kwargs):
        self.calls += 1
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return FRESH

    async def close(self):
        self.closed = True


@pytest.mark.parametrize('force', [False, True])
def test_simultaneous_body_reads_share_one_request(tmp_cache, force):
    async def run():
        if force:
            tmp_cache.set(KEY, {'records': []})
        client = GatedClient()
        service = DataService(tmp_cache, client)
        first = asyncio.create_task(service.get_body(START, END, force_refresh=force))
        await client.entered.wait()
        second = asyncio.create_task(service.get_body(START, END, force_refresh=force))
        await asyncio.sleep(0)
        client.release.set()
        result = await asyncio.gather(first, second)
        assert result == [FRESH, FRESH]
        assert client.calls == 1
        assert tmp_cache.get(KEY) == FRESH
        assert not service._body_requests

    asyncio.run(asyncio.wait_for(run(), timeout=2))


def test_cancelling_one_reader_does_not_cancel_other_reader(tmp_cache):
    async def run():
        client = GatedClient()
        service = DataService(tmp_cache, client)
        first = asyncio.create_task(service.get_body(START, END))
        await client.entered.wait()
        second = asyncio.create_task(service.get_body(START, END))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not client.cancelled
        client.release.set()
        assert await second == FRESH
        assert client.calls == 1
        assert not service._body_requests

    asyncio.run(asyncio.wait_for(run(), timeout=2))


def test_last_reader_cancellation_stops_request_and_releases_entry(tmp_cache):
    async def run():
        client = GatedClient()
        service = DataService(tmp_cache, client)
        reader = asyncio.create_task(service.get_body(START, END))
        await client.entered.wait()
        reader.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reader
        assert client.cancelled
        assert not service._body_requests
        assert tmp_cache.get(KEY) is None

    asyncio.run(asyncio.wait_for(run(), timeout=2))


def test_service_close_cancels_and_joins_pending_body_work(tmp_cache):
    async def run():
        client = GatedClient()
        service = DataService(tmp_cache, client)
        reader = asyncio.create_task(service.get_body(START, END))
        await client.entered.wait()
        await service.close()
        with pytest.raises(asyncio.CancelledError):
            await reader
        assert client.cancelled and client.closed
        assert not service._body_requests
        with pytest.raises(RuntimeError, match='closed'):
            await service.get_body(START, END)

    asyncio.run(asyncio.wait_for(run(), timeout=2))


def test_failed_shared_request_is_released_for_later_retry(tmp_cache):
    async def run():
        class Client(GatedClient):
            async def query_body(self, *args, **kwargs):
                self.calls += 1
                self.entered.set()
                await self.release.wait()
                if self.calls == 1:
                    raise RuntimeError('upstream unavailable')
                return FRESH

        client = Client()
        service = DataService(tmp_cache, client)
        first = asyncio.create_task(service.get_body(START, END))
        await client.entered.wait()
        second = asyncio.create_task(service.get_body(START, END))
        await asyncio.sleep(0)
        client.release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        assert all(isinstance(result, RuntimeError) for result in results)
        assert client.calls == 1
        assert not service._body_requests
        assert tmp_cache.get(KEY) is None
        assert await service.get_body(START, END) == FRESH
        assert client.calls == 2
        assert not service._body_requests

    asyncio.run(asyncio.wait_for(run(), timeout=2))
