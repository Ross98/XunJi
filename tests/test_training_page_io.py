import asyncio
import threading
from datetime import date

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

import routes
from routes import training


@pytest.fixture
def training_runtime(tmp_cache, monkeypatch):
    today = date.today().isoformat()
    payload = {'trains': [{'datestr': today, 'movements': [
        {'name': '卧推', 'sets': [{'weight': 50, 'reps': 10}]}]}]}
    tmp_cache.set(f'training:{today}:True', payload)

    class Service:
        async def get_training(self, day, **kwargs):
            return payload

    monkeypatch.setattr(training, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(training, 'get_data_service', Service)
    monkeypatch.setattr(training, '_ensure_training_history_backfill', lambda *args: None)
    return tmp_cache, today, payload


def request_route(route, today):
    request = Request({'type': 'http', 'path': '/training', 'headers': []})
    if route == 'page':
        return training.training_page(request)
    if route == 'calendar':
        return training.training_calendar_fragment(request, int(today[:4]), int(today[5:7]))
    return training.training_detail_fragment(request, today)


@pytest.mark.parametrize('route', ['page', 'calendar', 'detail'])
@pytest.mark.parametrize('stage', ['cache', 'render'])
def test_training_reads_and_renders_off_loop(training_runtime, monkeypatch, route, stage):
    owner = threading.get_ident()
    threads = []
    if stage == 'cache':
        original = training.get_cached_training

        def observed(*args, **kwargs):
            threads.append(threading.get_ident())
            return original(*args, **kwargs)
        monkeypatch.setattr(training, 'get_cached_training', observed)
    else:
        env = training.get_jinja_env()

        class Templates:
            def get_template(self, name):
                threads.append(threading.get_ident())
                return env.get_template(name)
        monkeypatch.setattr(training, 'get_jinja_env', Templates)
    response = asyncio.run(request_route(route, training_runtime[1]))
    assert response.status_code == 200
    assert threads and all(thread != owner for thread in threads)


@pytest.mark.parametrize('route', ['page', 'calendar'])
def test_page_projections_load_each_cached_day_once(training_runtime, monkeypatch, route):
    original = training.get_cached_training
    days = []

    def observed(cache, day, *args, **kwargs):
        days.append(day)
        return original(cache, day, *args, **kwargs)
    monkeypatch.setattr(training, 'get_cached_training', observed)
    asyncio.run(request_route(route, training_runtime[1]))
    assert days == [training_runtime[1]]


def test_first_today_fetch_appears_in_calendar_and_charts(training_runtime, monkeypatch):
    cache, today, payload = training_runtime
    cache.delete(f'training:{today}:True')
    context = {}

    class Service:
        async def get_training(self, day, **kwargs):
            cache.set(f'training:{today}:True', payload)
            return payload

    class Template:
        def render(self, **kwargs):
            context.update(kwargs)
            return 'ok'

    class Templates:
        def get_template(self, name):
            return Template()

    monkeypatch.setattr(training, 'get_data_service', Service)
    monkeypatch.setattr(training, 'get_jinja_env', Templates)
    asyncio.run(request_route('page', today))
    cell = next(day for week in context['cal']['weeks'] for day in week if day.get('date_str') == today)
    assert cell['has_data'] is True
    assert context['freq_counts'] == {today: 1}
    assert context['volume_data']['30'] == [{'date': today, 'value': 500}]
    assert context['summary']['total_sets'] == 1


@pytest.mark.parametrize('route', ['page', 'calendar', 'detail'])
@pytest.mark.parametrize('reader_error', [False, True])
def test_slow_training_read_keeps_quit_responsive_and_is_joined(training_runtime, monkeypatch, route, reader_error):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = training.get_cached_training

    def slow(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(3)
            if reader_error:
                raise OSError('synthetic cache failure')
            return original(*args, **kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(training, 'get_cached_training', slow)
    scheduled = []
    monkeypatch.setattr(routes, 'schedule_shutdown', lambda **kwargs: scheduled.append(kwargs))
    app = FastAPI()
    routes.register_routes(app)
    today = training_runtime[1]
    path = {'page': '/training', 'calendar': f'/training/calendar/{int(today[:4])}/{int(today[5:7])}',
            'detail': '/training/' + today}[route]

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 53000)), base_url='http://local') as client:
            pending = asyncio.create_task(client.get(path))
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


def test_history_backfill_cache_check_runs_off_loop(training_runtime, monkeypatch):
    owner = threading.get_ident()
    threads = []
    original = training._should_skip_training_backfill

    def observed(*args, **kwargs):
        threads.append(threading.get_ident())
        return original(*args, **kwargs)
    monkeypatch.setattr(training, '_should_skip_training_backfill', observed)

    class Service:
        async def get_training(self, *args, **kwargs):
            raise AssertionError('populated full cache must be skipped')
    asyncio.run(training._backfill_training_history(Service(), training_runtime[0], days=1, delay_seconds=0))
    assert threads and all(thread != owner for thread in threads)


@pytest.mark.parametrize('reader_error', [False, True])
def test_cancelled_backfill_joins_cache_check_and_starts_no_upstream(training_runtime, monkeypatch, reader_error):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def slow(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(3)
            if reader_error:
                raise OSError('synthetic cache failure')
            return False
        finally:
            finished.set()
    monkeypatch.setattr(training, '_should_skip_training_backfill', slow)

    class Service:
        async def get_training(self, *args, **kwargs):
            calls.append(args)

    async def run():
        task = asyncio.create_task(training._backfill_training_history(Service(), training_runtime[0], days=1, delay_seconds=0))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            await asyncio.sleep(.02)
            task.cancel()
            await asyncio.sleep(.02)
            assert not task.done() and not finished.is_set()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert finished.is_set()
        assert not calls
    asyncio.run(run())
