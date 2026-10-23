import asyncio

import httpx
import pytest
from fastapi import FastAPI

from routes import training


@pytest.mark.parametrize('path', [
    '/training/not-a-date', '/training/2026-02-30', '/training/2026-1-1',
    '/training/20261022', '/training/calendar/2026/0', '/training/calendar/2026/13',
    '/training/calendar/0/1', '/training/calendar/10000/1',
])
def test_invalid_training_paths_reject_before_data_access(path, monkeypatch):
    def unexpected():
        raise AssertionError('invalid input must not access local or upstream data')

    monkeypatch.setattr(training, 'get_cache', unexpected)
    monkeypatch.setattr(training, 'get_data_service', unexpected)
    app = FastAPI()
    app.include_router(training.router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
            response = await client.get(path)
            assert response.status_code == 422

    asyncio.run(run())


@pytest.mark.parametrize('year,month,blocked,allowed', [
    (1, 1, '/training/calendar/0/12', '/training/calendar/1/2'),
    (9999, 12, '/training/calendar/10000/1', '/training/calendar/9999/11'),
])
def test_calendar_boundary_navigation_never_generates_out_of_range_year(year, month, blocked, allowed):
    from app_state import get_jinja_env
    calendar = training._build_calendar(year, month, set())
    html = get_jinja_env().get_template('training_calendar.html').render(cal=calendar)
    assert blocked not in html
    assert allowed in html
    assert 'disabled' in html


@pytest.mark.parametrize('day', ['2026-1-1', '20261022', '2026-W43-4', '2026-02-30'])
def test_cache_calendar_identity_requires_canonical_date(day):
    assert training._parse_date_from_cache_key('training:'+day+':True') is None


def test_normal_calendar_cross_year_links_remain_enabled():
    from app_state import get_jinja_env
    calendar = training._build_calendar(2026, 12, set())
    html = get_jinja_env().get_template('training_calendar.html').render(cal=calendar)
    assert '/training/calendar/2027/1' in html
    assert '/training/calendar/2026/11' in html
    assert 'disabled' not in html


def test_valid_leap_day_detail_still_reads_and_renders(monkeypatch, tmp_cache):
    calls = []

    class Service:
        async def get_training(self, day, full=False):
            calls.append((day, full))
            return {'trains': []}

    monkeypatch.setattr(training, 'get_data_service', Service)
    monkeypatch.setattr(training, 'get_cache', lambda: tmp_cache)
    app = FastAPI()
    app.include_router(training.router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
            response = await client.get('/training/2024-02-29')
            assert response.status_code == 200
            assert '2024-02-29' in response.text

    asyncio.run(run())
    assert calls == [('2024-02-29', True)]
