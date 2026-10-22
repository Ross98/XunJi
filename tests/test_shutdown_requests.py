import asyncio

import httpx
import pytest
from fastapi import BackgroundTasks, FastAPI

import routes
import shutdown_service


def test_quit_drains_pending_requests_and_rejects_new_work(monkeypatch):
    async def run():
        app = FastAPI()
        routes.register_routes(app)
        entered = asyncio.Event()
        cancelled = []
        scheduled = []

        @app.get('/slow')
        async def slow():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)

        monkeypatch.setattr(routes, 'schedule_shutdown', lambda **kwargs: scheduled.append(kwargs))
        transport = httpx.ASGITransport(app=app, client=('127.0.0.1', 53000))
        async with httpx.AsyncClient(transport=transport, base_url='http://local') as browser:
            waiting = asyncio.create_task(browser.get('/slow'))
            await entered.wait()
            response = await browser.post('/admin/shutdown', headers={'X-Xunji-Shutdown': 'confirm'})
            assert response.status_code == 202
            assert (await browser.get('/slow')).status_code == 503
            await scheduled[0]['before_signal']()
            assert (await waiting).status_code == 503
            assert cancelled == [True]
            assert not app.state.shutdown_requests.tasks

    asyncio.run(asyncio.wait_for(run(), timeout=2))


def test_quit_cancels_response_background_work_without_replacing_response(monkeypatch):
    async def run():
        app = FastAPI()
        routes.register_routes(app)
        entered = asyncio.Event()
        cancelled = []
        scheduled = []

        async def background():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)

        @app.get('/background')
        async def page(tasks: BackgroundTasks):
            tasks.add_task(background)
            return {'ok': True}

        monkeypatch.setattr(routes, 'schedule_shutdown', lambda **kwargs: scheduled.append(kwargs))
        transport = httpx.ASGITransport(app=app, client=('127.0.0.1', 53000))
        async with httpx.AsyncClient(transport=transport, base_url='http://local') as browser:
            pending = asyncio.create_task(browser.get('/background'))
            await entered.wait()
            await browser.post('/admin/shutdown', headers={'X-Xunji-Shutdown': 'confirm'})
            await scheduled[0]['before_signal']()
            assert (await pending).json() == {'ok': True}
            assert cancelled == [True]

    asyncio.run(asyncio.wait_for(run(), timeout=2))


def test_shutdown_drains_before_signalling_process(monkeypatch):
    calls = []

    async def before_signal():
        calls.append('drained')

    monkeypatch.setattr(shutdown_service.os, 'kill', lambda *args: calls.append('signal'))
    asyncio.run(shutdown_service._shutdown_after_response(0, before_signal=before_signal))
    assert calls == ['drained', 'signal']


def test_cleanup_failure_still_signals_shutdown(monkeypatch):
    calls = []

    async def fail():
        raise RuntimeError('cleanup failure')

    monkeypatch.setattr(shutdown_service.os, 'kill', lambda *args: calls.append('signal'))
    with pytest.raises(RuntimeError, match='cleanup failure'):
        asyncio.run(shutdown_service._shutdown_after_response(0, before_signal=fail))
    assert calls == ['signal']


def test_normal_request_cancellation_is_not_hidden():
    async def run():
        app = FastAPI()
        routes.register_routes(app)
        entered = asyncio.Event()

        @app.get('/waiting')
        async def waiting():
            entered.set()
            await asyncio.Event().wait()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://local'
        ) as browser:
            request = asyncio.create_task(browser.get('/waiting'))
            await entered.wait()
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert not app.state.shutdown_requests.tasks
            assert not app.state.shutdown_requests.closing

    asyncio.run(asyncio.wait_for(run(), timeout=2))
