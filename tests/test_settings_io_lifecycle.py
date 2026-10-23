"""Local settings/cache I/O stays responsive and finishes before request drain."""
import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import FastAPI

import app_state
import routes
from routes import body, plan


@pytest.fixture(autouse=True)
def isolated_local_files(tmp_path, tmp_cache, monkeypatch):
    monkeypatch.setattr(plan, 'PLAN_CONFIG_PATH', tmp_path/'plan.json')
    monkeypatch.setattr(body, 'BODY_CONFIG_PATH', str(tmp_path/'body.json'))
    monkeypatch.setattr(app_state, 'get_cache', lambda: tmp_cache)


def application():
    app = FastAPI()
    routes.register_routes(app)
    return app


@pytest.mark.parametrize('module,name,path,payload', [
    (plan, '_load_plan_preferences', '/plan/settings', {'days_per_week': 2}),
    (plan, '_save_plan_preferences', '/plan/settings', {'days_per_week': 2}),
    (body, '_load_body_config', '/body/settings', {'height_cm': 181}),
    (body, '_save_body_config', '/body/settings', {'height_cm': 181}),
    (None, 'clear', '/admin/clear-cache', {}),
    (None, 'get', '/admin/refresh-times', None),
])
def test_local_io_runs_off_event_loop(tmp_cache, monkeypatch, module, name, path, payload):
    module = module if module is not None else tmp_cache
    original = getattr(module, name)
    owner = threading.get_ident()
    threads = []
    def observed(*args, **kwargs):
        threads.append(threading.get_ident())
        return original(*args, **kwargs)
    monkeypatch.setattr(module, name, observed)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application()),base_url='http://local') as client:
            response = await client.get(path) if payload is None else await client.post(path,json=payload)
            assert response.status_code == 200
    asyncio.run(run())
    assert threads and all(thread != owner for thread in threads)


@pytest.mark.parametrize('kind', ['plan', 'body', 'clear', 'refresh'])
def test_quit_waits_for_local_io_while_authorization_stays_responsive(tmp_cache, monkeypatch, kind):
    tmp_cache.set('training:synthetic', {'trains': []})
    marker = {'time': '2026-10-22T00:00:00Z'}
    tmp_cache.set('_health_import_time', marker)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    module, name, path, payload = {
        'plan': (plan, '_save_plan_preferences', '/plan/settings', {'days_per_week':2}),
        'body': (body, '_save_body_config', '/body/settings', {'height_cm':181}),
        'clear': (tmp_cache, 'clear', '/admin/clear-cache', {}),
        'refresh': (tmp_cache, 'get', '/admin/refresh-times', None),
    }[kind]
    original = getattr(module,name)
    def slow(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(2), 'Local I/O blocked request handling'
            return original(*args, **kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(module,name,slow)
    scheduled = []
    monkeypatch.setattr(routes,'schedule_shutdown',lambda **kwargs:scheduled.append(kwargs))
    app = application()
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',53000)),base_url='http://local') as client:
            operation = client.get(path) if payload is None else client.post(path,json=payload)
            pending = asyncio.create_task(operation)
            drain = None
            try:
                assert await asyncio.to_thread(entered.wait,1)
                response = await asyncio.wait_for(client.post('/admin/shutdown'),.5)
                assert response.status_code == 409
                assert not pending.done()
                response = await client.post('/admin/shutdown',headers={'X-Xunji-Shutdown':'confirm'})
                assert response.status_code == 202
                draining = asyncio.Event()
                async def stop():
                    draining.set()
                    await scheduled[0]['before_signal']()
                drain = asyncio.create_task(stop())
                await draining.wait()
                await asyncio.sleep(.02)  # Let request cancellation reach its worker wait.
                assert not drain.done()
                assert not finished.is_set()
            finally:
                release.set()
                if drain is not None:
                    await drain
                response = await pending
                assert await asyncio.to_thread(finished.wait,1)
            assert response.status_code == 503
            assert finished.is_set()
            assert not app.state.shutdown_requests.tasks
    asyncio.run(run())
    from pathlib import Path
    if kind == 'plan':
        assert json.loads(Path(plan.PLAN_CONFIG_PATH).read_text())['days_per_week'] == 2
    elif kind == 'body':
        assert json.loads(Path(body.BODY_CONFIG_PATH).read_text())['height_cm'] == 181
    elif kind == 'clear':
        assert tmp_cache.get('training:synthetic') is None
        assert tmp_cache.get('_health_import_time') == marker


@pytest.mark.parametrize('kind', ['plan', 'body'])
def test_concurrent_partial_updates_do_not_restore_old_preferences(monkeypatch, kind):
    module, loader, update, first, second, expected, path = {
        'plan': (plan, '_load_plan_preferences', plan.update_plan_settings,
                 {'days_per_week':2}, {'session_minutes':60},
                 {'days_per_week':2,'session_minutes':60}, plan.PLAN_CONFIG_PATH),
        'body': (body, '_load_body_config', body.update_body_settings,
                 {'height_cm':181}, {}, {'height_cm':181}, body.BODY_CONFIG_PATH),
    }[kind]
    original = getattr(module,loader)
    saver = '_save_plan_preferences' if kind == 'plan' else '_save_body_config'
    original_save = getattr(module,saver)
    first_loaded, first_saved = threading.Event(), threading.Event()
    second_loaded = threading.Event()
    counter_lock = threading.Lock()
    loads = []
    def overlapping_load():
        snapshot = original()
        with counter_lock:
            loads.append(True)
            index = len(loads)
        if index == 1:
            # Without serialization, the second reader receives the same old
            # snapshot before either updater commits its different fields.
            first_loaded.set()
            second_loaded.wait(.2)
        else:
            second_loaded.set()
            assert first_saved.wait(1)
        return snapshot
    def save(snapshot):
        original_save(snapshot)
        first_saved.set()
    monkeypatch.setattr(module,loader,overlapping_load)
    monkeypatch.setattr(module,saver,save)
    with ThreadPoolExecutor(max_workers=2) as executor:
        tasks = [executor.submit(lambda:asyncio.run(update(first)))]
        assert first_loaded.wait(1)
        tasks.append(executor.submit(lambda:asyncio.run(update(second))))
        assert all(task.result(timeout=3)['status']=='ok' for task in tasks)
    from pathlib import Path
    saved = json.loads(Path(path).read_text())
    assert all(saved[key]==value for key,value in expected.items())
