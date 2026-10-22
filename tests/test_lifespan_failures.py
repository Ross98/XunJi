import asyncio

import pytest

import app as application
import routes
from routes import health, training


@pytest.fixture(autouse=True)
def isolated_initialization(monkeypatch):
    monkeypatch.setattr(application, 'initialize_health_store', lambda: None)


def test_http_client_closes_even_when_job_cleanup_fails(monkeypatch):
    calls = []

    async def start():
        calls.append('start')

    async def stop():
        calls.append('stop')
        raise RuntimeError('cleanup failed')

    async def close():
        calls.append('close')

    monkeypatch.setattr(application, 'get_data_service', lambda: object())
    monkeypatch.setattr(health, 'start_health_background_tasks', start)
    monkeypatch.setattr(application, 'close_route_tasks', stop)
    monkeypatch.setattr(application, 'close_state', close)

    async def run():
        with pytest.raises(RuntimeError, match='cleanup failed'):
            async with application.lifespan(application.app):
                pass

    asyncio.run(run())
    assert calls == ['start', 'stop', 'close']


def test_startup_failure_also_releases_clients_and_jobs(monkeypatch):
    calls = []

    async def start():
        raise RuntimeError('startup failed')

    async def stop():
        calls.append('stop')

    async def close():
        calls.append('close')

    monkeypatch.setattr(application, 'get_data_service', lambda: object())
    monkeypatch.setattr(health, 'start_health_background_tasks', start)
    monkeypatch.setattr(application, 'close_route_tasks', stop)
    monkeypatch.setattr(application, 'close_state', close)

    async def run():
        with pytest.raises(RuntimeError, match='startup failed'):
            async with application.lifespan(application.app):
                pass

    asyncio.run(run())
    assert calls == ['stop', 'close']


def test_all_job_cleanup_finishes_before_error_is_propagated(monkeypatch):
    calls = []

    async def stop_training():
        raise RuntimeError('training cleanup failed')

    async def stop_health():
        await asyncio.sleep(0.01)
        calls.append('health stopped')

    monkeypatch.setattr(training, 'stop_training_background_tasks', stop_training)
    monkeypatch.setattr(health, 'stop_health_background_tasks', stop_health)

    async def run():
        with pytest.raises(RuntimeError, match='training cleanup failed'):
            await routes.close_route_tasks()
        assert calls == ['health stopped']

    asyncio.run(run())


def test_failed_health_initialization_still_closes_resources(monkeypatch):
    calls = []

    def initialize():
        raise OSError('simulated initialization error')

    async def stop():
        calls.append('stop')

    async def close():
        calls.append('close')

    monkeypatch.setattr(application, 'get_data_service', lambda: object())
    monkeypatch.setattr(application, 'initialize_health_store', initialize)
    monkeypatch.setattr(application, 'close_route_tasks', stop)
    monkeypatch.setattr(application, 'close_state', close)

    async def run():
        with pytest.raises(OSError, match='initialization error'):
            async with application.lifespan(application.app):
                pass

    asyncio.run(run())
    assert calls == ['stop', 'close']
