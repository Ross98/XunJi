"""Slow plan reads must not block status/quit, and quit must join the reader."""
import asyncio
import threading

import httpx
import pytest
from fastapi import FastAPI

import routes
from routes import plan


@pytest.fixture(autouse=True)
def isolated_plan(isolated_health_db, tmp_cache, tmp_path, monkeypatch):
    monkeypatch.setattr(plan, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(plan, 'PLAN_CONFIG_PATH', tmp_path/'plan.json')


@pytest.mark.parametrize('stage', ['cache', 'sleep', 'preferences', 'generation', 'rendering'])
def test_plan_synchronous_work_runs_outside_request_loop(stage, isolated_health_db, monkeypatch):
    owner = threading.get_ident()
    module, name = {
        'cache': (plan, 'get_cached_training'),
        'sleep': (isolated_health_db, 'get_sleep_summary'),
        'preferences': (plan, '_load_plan_preferences'),
        'generation': (plan, 'generate_plan'),
        'rendering': (plan, 'get_jinja_env'),
    }[stage]
    original = getattr(module, name)
    threads = []
    def observed(*args, **kwargs):
        threads.append(threading.get_ident())
        return original(*args, **kwargs)
    monkeypatch.setattr(module, name, observed)
    app = FastAPI()
    app.include_router(plan.router)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response = await client.get('/plan')
        assert response.status_code == 200
    asyncio.run(run())
    assert threads and all(thread != owner for thread in threads)


@pytest.mark.parametrize('reader_error', [False, True])
def test_slow_plan_allows_quit_and_reader_finishes_before_drain(monkeypatch, reader_error):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    render_finished = threading.Event()
    original_render = plan._render_plan_page
    def render(*args, **kwargs):
        try:
            return original_render(*args, **kwargs)
        finally:
            render_finished.set()
    monkeypatch.setattr(plan, '_render_plan_page', render)
    original = plan.get_cached_training
    first = True
    def slow_read(*args, **kwargs):
        nonlocal first
        if first:
            first = False
            entered.set()
            try:
                assert release.wait(2), 'plan read blocked the request loop'
            finally:
                finished.set()
            if reader_error:
                raise OSError('synthetic reader failure after cancellation')
        return original(*args, **kwargs)
    monkeypatch.setattr(plan, 'get_cached_training', slow_read)
    scheduled = []
    monkeypatch.setattr(routes, 'schedule_shutdown', lambda **kwargs: scheduled.append(kwargs))
    app = FastAPI()
    routes.register_routes(app)
    @app.get('/ping')
    async def ping():
        return {'ok': True}
    async def run():
        transport = httpx.ASGITransport(app=app,client=('127.0.0.1',53000))
        async with httpx.AsyncClient(transport=transport,base_url='http://local') as client:
            pending = asyncio.create_task(client.get('/plan'))
            drain = None
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                response = await asyncio.wait_for(client.get('/ping'), .5)
                assert response.status_code == 200
                assert not pending.done()
                quit_response = await client.post('/admin/shutdown',headers={'X-Xunji-Shutdown':'confirm'})
                assert quit_response.status_code == 202
                draining = asyncio.Event()
                async def stop():
                    draining.set()
                    await scheduled[0]['before_signal']()
                drain = asyncio.create_task(stop())
                await draining.wait()
                assert not drain.done()
                assert not finished.is_set()
            finally:
                release.set()
                if drain is not None:
                    await drain
                response = await pending
            assert finished.is_set()
            assert render_finished.is_set()
            assert response.status_code == 503
            assert not app.state.shutdown_requests.tasks
    asyncio.run(run())


def test_plan_uses_one_date_snapshot_even_if_midnight_passes(monkeypatch, tmp_path, isolated_health_db):
    from datetime import date
    from starlette.requests import Request
    calls = []
    class Midnight(date):
        @classmethod
        def today(cls):
            calls.append(True)
            return cls(2026, 10, 22) if len(calls) == 1 else cls(2026, 10, 23)
    monkeypatch.setattr(plan, 'date', Midnight)
    days = []
    monkeypatch.setattr(plan, 'get_cached_training', lambda cache, day: days.append(day))
    monkeypatch.setattr(isolated_health_db, 'HEALTH_DB_PATH', tmp_path/'missing.sqlite')
    captured = {}
    original = plan.generate_plan
    def generate(*args, **kwargs):
        captured['reference'] = kwargs['reference_date'].isoformat()
        return original(*args, **kwargs)
    monkeypatch.setattr(plan, 'generate_plan', generate)
    class Template:
        def render(self, **kwargs):
            captured.update(kwargs)
            return 'ok'
    class Templates:
        def get_template(self, name):
            return Template()
    monkeypatch.setattr(plan, 'get_jinja_env', Templates)
    asyncio.run(plan.plan_page(Request({'type':'http'})))
    assert days[0] == '2026-10-22'
    assert days[-1] == '2026-09-23'
    assert len(set(days)) == 30
    assert captured['reference'] == captured['today'] == '2026-10-22'
    assert len(calls) == 1
