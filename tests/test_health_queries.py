"""Public health queries must stay bounded and leave the event loop responsive."""

import asyncio
import threading

import httpx
import pytest
from fastapi import FastAPI

from routes import health


@pytest.mark.parametrize('endpoint,query', [
    ('/health/data', 'query_records'), ('/health/workouts', 'query_workouts'),
])
@pytest.mark.parametrize('limit', ['0', '-1', '1001', 'not-a-number'])
def test_invalid_health_limit_rejects_before_database_access(monkeypatch, endpoint, query, limit):
    def unexpected(*args):
        raise AssertionError('invalid limit reached database')

    monkeypatch.setattr(health.ah, query, unexpected)
    app = FastAPI()
    app.include_router(health.router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
            response = await client.get(endpoint, params={'limit': limit})
            assert response.status_code == 422

    asyncio.run(run())


@pytest.mark.parametrize('endpoint,query,default', [
    ('/health/data', 'query_records', 100), ('/health/workouts', 'query_workouts', 50),
])
@pytest.mark.parametrize('limit', [None, 1, 1000])
def test_health_queries_keep_filters_and_run_outside_event_loop(
    monkeypatch, endpoint, query, default, limit
):
    calls = []
    owner_thread = threading.get_ident()

    def read(*args):
        calls.append(args)
        assert threading.get_ident() != owner_thread, 'SQLite read blocked the event loop'
        return [{'source_name': 'synthetic', 'value': 0}]

    monkeypatch.setattr(health.ah, query, read)
    app = FastAPI()
    app.include_router(health.router)

    async def run():
        params = {'start': '2026-08-01', 'end': '2026-08-09'}
        params['metric' if query == 'query_records' else 'activity'] = 'test-type'
        if limit is not None:
            params['limit'] = limit
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
            response = await client.get(endpoint, params=params)
            assert response.status_code == 200
            assert response.json() == {'data': [{'source_name': 'synthetic', 'value': 0}]}

    asyncio.run(run())
    assert calls == [('test-type', '2026-08-01', '2026-08-09', default if limit is None else limit)]


def test_health_overview_database_reads_run_outside_event_loop(
    isolated_health_db, tmp_cache, monkeypatch, tmp_path
):
    owner_thread = threading.get_ident()
    original = isolated_health_db.get_import_count
    calls = []

    def read_counts():
        assert threading.get_ident() != owner_thread, 'health overview blocked the event loop'
        calls.append('counts')
        return original()

    monkeypatch.setattr(isolated_health_db, 'get_import_count', read_counts)
    monkeypatch.setattr(isolated_health_db, 'EXPORT_XML', tmp_path / 'missing.xml')
    monkeypatch.setattr(health, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(health.hs, 'source_folder', lambda: tmp_path)
    app = FastAPI()
    app.include_router(health.router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
            response = await client.get('/health')
            assert response.status_code == 200
            assert '尚未导入 Apple Health' in response.text

    asyncio.run(run())
    assert calls == ['counts']


@pytest.mark.parametrize('endpoint,query', [
    ('/health', 'get_import_count'),
    ('/health/data', 'query_records'),
    ('/health/workouts', 'query_workouts'),
])
def test_slow_health_read_does_not_delay_status_request(
    isolated_health_db, tmp_cache, monkeypatch, tmp_path, endpoint, query
):
    entered = threading.Event()
    release = threading.Event()
    original = getattr(isolated_health_db, query)

    def slow_read(*args):
        entered.set()
        assert release.wait(2), 'status request could not run during a slow health read'
        return original(*args)

    monkeypatch.setattr(isolated_health_db, query, slow_read)
    monkeypatch.setattr(isolated_health_db, 'EXPORT_XML', tmp_path / 'missing.xml')
    monkeypatch.setattr(health, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(health.hs, 'source_folder', lambda: tmp_path)
    app = FastAPI()
    app.include_router(health.router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
            pending = asyncio.create_task(client.get(endpoint))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                status = await asyncio.wait_for(client.get('/health/import/status'), 1)
                assert status.status_code == 200
                assert not pending.done()
            finally:
                release.set()
                response = await pending
            assert response.status_code == 200

    asyncio.run(run())


def test_default_health_metric_returns_imported_hrv(isolated_health_db):
    isolated_health_db._bulk_insert([{
        'type': 'HKQuantityTypeIdentifierHeartRateVariabilitySDNN',
        'start_date': '2026-08-09 08:00:00 +0800',
        'end_date': '2026-08-09 08:01:00 +0800',
        'value': 42, 'unit': 'ms', 'source_name': 'synthetic',
    }], [])
    app = FastAPI()
    app.include_router(health.router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
            response = await client.get('/health/data')
            assert response.status_code == 200
            records = response.json()['data']
            assert len(records) == 1
            assert records[0]['value'] == 42
            assert records[0]['type'] == 'HKQuantityTypeIdentifierHeartRateVariabilitySDNN'

    asyncio.run(run())
