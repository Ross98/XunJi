import asyncio
from datetime import date, timedelta

import pytest
from starlette.requests import Request

from routes import body


@pytest.mark.parametrize('selected,days', [('30', 30), ('90', 90), ('180', 180), ('365', 365), ('all', None), ('invalid', 90)])
def test_body_query_uses_inclusive_calendar_window(
    selected, days, monkeypatch, isolated_health_db, tmp_cache, tmp_path
):
    calls = []
    today = date.today()

    class Service:
        async def get_body(self, start, end):
            calls.append((start, end))
            return {'records': []}

    monkeypatch.setattr(body, 'get_data_service', Service)
    monkeypatch.setattr(body, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(body, 'BODY_CONFIG_PATH', str(tmp_path / 'body.json'))
    asyncio.run(body.body_page(Request({'type': 'http', 'path': '/body', 'headers': []}), range=selected))
    expected = (today - timedelta(days=days - 1)).isoformat() if days else None
    assert calls == [(expected, today.isoformat())]


def test_body_merge_excludes_future_and_out_of_window_api_records(isolated_health_db):
    records = [{'datestr': day, 'type': 'weight', 'value': 70}
               for day in ['2026-10-24', '2026-10-23', '2026-09-24', '2026-09-23']]
    result, _ = body._merge_apple_health(records, '2026-09-24', '2026-10-23')
    assert [r['datestr'] for r in result] == ['2026-10-23', '2026-09-24']
    assert len(records) == 4


def test_all_body_history_keeps_records_older_than_ten_years(
    monkeypatch, isolated_health_db, tmp_cache, tmp_path
):
    class Service:
        async def get_body(self, start, end):
            assert start is None
            return {'records': [{'datestr': '2010-01-01', 'type': 'weight', 'value': 70}]}

    monkeypatch.setattr(body, 'get_data_service', Service)
    monkeypatch.setattr(body, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(body, 'BODY_CONFIG_PATH', str(tmp_path / 'body.json'))
    response = asyncio.run(body.body_page(Request({'type': 'http', 'path': '/body', 'headers': []}), range='all'))
    assert '<td>2010-01-01</td>' in response.body.decode()
