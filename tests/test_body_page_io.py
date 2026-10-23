import asyncio
import threading
from datetime import date

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

import routes
from routes import body


@pytest.fixture
def body_runtime(isolated_health_db, tmp_cache, tmp_path, monkeypatch):
    payload = {'records': [{'type': 'bodyfat', 'datestr': date.today().isoformat(), 'value': 0}]}

    class Service:
        async def get_body(self, *args, **kwargs):
            return payload

    monkeypatch.setattr(body, 'get_data_service', Service)
    monkeypatch.setattr(body, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(body, 'BODY_CONFIG_PATH', str(tmp_path / 'body.json'))
    return payload


def render_page():
    return body.body_page(Request({'type': 'http', 'path': '/body', 'headers': []}), range='all')


@pytest.mark.parametrize('stage', ['_merge_apple_health', '_load_body_config', '_build_table_data', 'get_jinja_env'])
def test_body_local_reads_and_rendering_run_off_loop(body_runtime, monkeypatch, stage):
    owner = threading.get_ident()
    threads = []
    original = getattr(body, stage)

    def observed(*args, **kwargs):
        threads.append(threading.get_ident())
        return original(*args, **kwargs)
    monkeypatch.setattr(body, stage, observed)
    assert asyncio.run(render_page()).status_code == 200
    assert threads and all(thread != owner for thread in threads)


@pytest.mark.parametrize('zero', [0, 0.0, '0'])
def test_body_zero_is_consistent_in_summary_stats_and_history(body_runtime, zero):
    body_runtime['records'][0]['value'] = zero
    html = asyncio.run(render_page()).body.decode()
    stats = html.split('体脂率统计 (%)</div>', 1)[1].split('<!-- Data table -->', 1)[0]
    assert '最新' in stats and date.today().isoformat() in stats
    assert '>0' in stats
    assert '最新体脂率 (%)' in html
    assert 'body-history-row' in html


@pytest.mark.parametrize('records', [[], [{'type': 'weight', 'datestr': date.today().isoformat(), 'value': 70}]])
def test_missing_bodyfat_does_not_claim_a_latest_observation(body_runtime, records):
    body_runtime['records'] = records
    html = asyncio.run(render_page()).body.decode()
    stats = html.split('体脂率统计 (%)</div>', 1)[1].split('<!-- Data table -->', 1)[0]
    assert '最新' not in stats


@pytest.mark.parametrize('stage', ['_merge_apple_health', '_load_body_config'])
@pytest.mark.parametrize('reader_error', [False, True])
def test_slow_body_page_keeps_quit_responsive_and_joins_worker(body_runtime, monkeypatch, stage, reader_error):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = getattr(body, stage)

    def slow(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(3)
            if reader_error:
                raise OSError('synthetic local read failure')
            return original(*args, **kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(body, stage, slow)
    scheduled = []
    monkeypatch.setattr(routes, 'schedule_shutdown', lambda **kwargs: scheduled.append(kwargs))
    app = FastAPI()
    routes.register_routes(app)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 53000)), base_url='http://local') as client:
            pending = asyncio.create_task(client.get('/body?range=all'))
            drain = None
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                assert (await asyncio.wait_for(client.post('/admin/shutdown'), .5)).status_code == 409
                assert not pending.done()
                assert (await client.post('/admin/shutdown', headers={'X-Xunji-Shutdown': 'confirm'})).status_code == 202
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
            assert finished.is_set()
            assert response.status_code == 503
            assert not app.state.shutdown_requests.tasks
    asyncio.run(run())
