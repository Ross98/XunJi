"""A bad timestamp must not break status reads or block the event loop."""
import asyncio
import threading

import httpx
import pytest

import app_state
from app import app


@pytest.mark.parametrize('stamp,expected', [
    ('9999-12-31T23:00:00Z', None),
    ('0001-01-01T00:00:00+23:00', None),
    ('0001-01-01T00:00:00Z', '0001-01-01T08:00:00'),
    ('2026-10-23T08', '2026-10-23T08:00:00'),
    ('2026-10-23 08:00:00', '2026-10-23T08:00:00'),
    ('2026-10-23T08:00:00.123456', '2026-10-23T08:00:00'),
])
def test_one_boundary_timestamp_cannot_hide_the_other_marker(tmp_cache, monkeypatch, stamp, expected):
    good = '2026-10-23T00:00:00Z'
    tmp_cache.set('_xunji_refresh_time', {'time': stamp})
    tmp_cache.set('_health_import_time', {'time': good})
    monkeypatch.setattr(app_state, 'get_cache', lambda: tmp_cache)
    before = tmp_cache.get_mtime('_xunji_refresh_time')

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://local') as client:
            response = await client.get('/admin/refresh-times')
            assert response.status_code == 200
            assert response.json() == {'xunji':expected,'health':'2026-10-23T08:00:00'}
    asyncio.run(run())
    assert tmp_cache.get('_xunji_refresh_time') == {'time':stamp}
    assert tmp_cache.get_mtime('_xunji_refresh_time') == before


def test_slow_marker_read_does_not_block_exit_request_authorization(monkeypatch):
    entered,release = threading.Event(),threading.Event()
    owner = threading.get_ident()

    class SlowCache:
        def get(self,key):
            assert threading.get_ident() != owner,'Marker I/O blocked the event loop'
            entered.set()
            assert release.wait(2)
            return None
    monkeypatch.setattr(app_state,'get_cache',SlowCache)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
            pending=asyncio.create_task(client.get('/admin/refresh-times'))
            try:
                assert await asyncio.to_thread(entered.wait,1)
                rejected=await asyncio.wait_for(client.post('/admin/shutdown'),.5)
                assert rejected.status_code == 409
                assert not pending.done()
            finally:
                release.set()
                response=await pending
            assert response.status_code == 200
    asyncio.run(run())
