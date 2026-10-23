import asyncio
import threading

import httpx
import pytest
from fastapi import FastAPI

import routes
from routes import health


@pytest.mark.parametrize('endpoint,method,reader', [
    ('/health', 'get', '_render_health_page'),
    ('/health/data', 'get', 'query_records'),
    ('/health/workouts', 'get', 'query_workouts'),
    ('/health/sync/status', 'get', '_hozz_status_context'),
    ('/health/import/status', 'get', '_xml_export_info'),
    ('/health/import', 'post', '_xml_export_info'),
])
@pytest.mark.parametrize('reader_error', [False, True])
def test_health_shutdown_waits_for_actual_reader(monkeypatch, endpoint, method, reader, reader_error):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    module = health.ah if reader in ('query_records', 'query_workouts') else health
    health._reset_import_state_for_tests()

    def slow(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(3)
            if reader_error:
                raise OSError('synthetic read failure')
            return {'available': False} if reader == '_xml_export_info' else []
        finally:
            finished.set()
    monkeypatch.setattr(module, reader, slow)
    scheduled = []
    monkeypatch.setattr(routes, 'schedule_shutdown', lambda **kwargs: scheduled.append(kwargs))
    app = FastAPI()
    routes.register_routes(app)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 53000)), base_url='http://local') as client:
            pending = asyncio.create_task(client.request(method, endpoint))
            drain = None
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                response = await asyncio.wait_for(client.post('/admin/shutdown', headers={'X-Xunji-Shutdown': 'confirm'}), .5)
                assert response.status_code == 202
                draining = asyncio.Event()

                async def stop():
                    draining.set()
                    await scheduled[0]['before_signal']()
                drain = asyncio.create_task(stop())
                await draining.wait()
                await asyncio.sleep(.02)
                assert not drain.done() and not finished.is_set()
            finally:
                release.set()
                if drain is not None:
                    await drain
                response = await pending
            assert response.status_code == 503 and finished.is_set()
            assert not app.state.shutdown_requests.tasks
            assert health._import_task is None
    asyncio.run(run())
