import asyncio

import httpx
import pytest
from fastapi import FastAPI

from routes import health


@pytest.mark.parametrize('endpoint,reader', [('/health/data', 'query_records'), ('/health/workouts', 'query_workouts')])
@pytest.mark.parametrize('params', [
    {'start': ''}, {'end': ''}, {'start': 'bad'}, {'end': '2026-02-30'},
    {'start': '2026-1-1'}, {'end': '20261023'}, {'start': '2026-10-23T12:00:00Z'},
    {'start': '2026-10-24', 'end': '2026-10-23'}, {'end': '0000-01-01'}, {'start': '10000-01-01'},
])
def test_bad_query_window_is_rejected_before_database(monkeypatch, endpoint, reader, params):
    def unexpected(*args):
        raise AssertionError('invalid window reached database')
    monkeypatch.setattr(health.ah, reader, unexpected)
    app = FastAPI()
    app.include_router(health.router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://local') as client:
            assert (await client.get(endpoint, params=params)).status_code == 422
    asyncio.run(run())


@pytest.mark.parametrize('endpoint,reader', [('/health/data', 'query_records'), ('/health/workouts', 'query_workouts')])
@pytest.mark.parametrize('params', [
    {}, {'start': '0001-01-01'}, {'end': '9999-12-31'},
    {'start': '2024-02-29', 'end': '2024-02-29'},
])
def test_valid_query_windows_preserve_optional_inclusive_bounds(monkeypatch, endpoint, reader, params):
    calls = []

    def read(*args):
        calls.append(args)
        return []
    monkeypatch.setattr(health.ah, reader, read)
    app = FastAPI()
    app.include_router(health.router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
            assert (await client.get(endpoint, params=params)).json() == {'data': []}
    asyncio.run(run())
    assert calls[0][1:3] == (params.get('start'), params.get('end'))


@pytest.mark.parametrize('today_text', ['2026-01-01', '2024-03-01'])
def test_health_overview_uses_exactly_90_inclusive_days(isolated_health_db, tmp_cache, tmp_path, monkeypatch, today_text):
    from datetime import date, timedelta
    from starlette.requests import Request
    reference = date.fromisoformat(today_text)

    class Clock(date):
        @classmethod
        def today(cls):
            return reference
    monkeypatch.setattr(health, 'date', Clock)
    monkeypatch.setattr(health, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(health.hs, 'source_folder', lambda: tmp_path)
    monkeypatch.setattr(health, '_xml_export_info', lambda: {'available': False, 'size_mb': None})
    windows = []

    def records(metric, start, end, limit):
        windows.append((start, end))
        return []

    def sleep(start, end):
        windows.append((start, end))
        return []
    monkeypatch.setattr(health.ah, 'query_records_desc', records)
    monkeypatch.setattr(health.ah, 'get_sleep_summary', sleep)
    response = asyncio.run(health.health_page(Request({'type': 'http', 'path': '/health', 'headers': []})))
    assert response.status_code == 200
    assert len(windows) == 14
    assert set(windows) == {((reference - timedelta(days=89)).isoformat(), reference.isoformat())}
