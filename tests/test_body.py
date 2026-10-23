import asyncio

import pytest
from starlette.requests import Request

from routes import body
from analysis import body_changes


def test_merged_body_history_is_newest_first_for_change_calculation(isolated_health_db):
    ah = isolated_health_db
    ah._bulk_insert([
        dict(type="HKQuantityTypeIdentifierBodyMass", start_date=f"2026-10-{day} 08:00:00 +0800",
             end_date=f"2026-10-{day} 08:00:00 +0800", value=value, unit="kg", source_name="Watch")
        for day, value in ((21, 71), (22, 72))
    ], [])
    records = [{"datestr": "2026-10-20", "type": "weight", "value": "70"}]
    merged, _ = body._merge_apple_health(records, "2026-10-01", "2026-10-22")
    assert [row["datestr"] for row in merged] == ["2026-10-22", "2026-10-21", "2026-10-20"]
    assert body_changes(merged)["weight:2026-10-22"] == 1
    assert records == [{"datestr": "2026-10-20", "type": "weight", "value": "70"}]


def test_body_merge_preserves_xunji_priority(isolated_health_db):
    ah = isolated_health_db
    ah._bulk_insert([dict(type="HKQuantityTypeIdentifierBodyMass",
        start_date="2026-10-22 08:00:00 +0800", end_date="2026-10-22 08:00:00 +0800",
        value=71, unit="kg", source_name="Watch")], [])
    records = [{"datestr": "2026-10-22", "type": "weight", "value": "70"}]
    merged, _ = body._merge_apple_health(records, "2026-10-01", "2026-10-22")
    assert merged == records


@pytest.mark.parametrize("payload", [
    {"success": False, "res": "too frequent"},
    {"res": {"records": "{broken"}},
    {"res": {"records": None}},
    {"res": {"records": []}},
    {"records": [{"datestr": "2026-10-22", "type": "weight", "value": "unknown"} ]},
    {"records": [{"datestr": "2026-10-22", "type": "weight", "value": "NaN"} ]},
    {"res": {"records": [{"datestr": "2026-10-22", "type": "weight", "value": "70"}]}},
])
def test_body_page_tolerates_bad_api_payload(payload, monkeypatch, isolated_health_db, tmp_cache, tmp_path):
    class Service:
        async def get_body(self, *args, **kwargs):
            return payload

    monkeypatch.setattr(body, "get_data_service", Service)
    monkeypatch.setattr(body, "get_cache", lambda: tmp_cache)
    config = tmp_path / "body.json"
    config.write_text("{broken")
    monkeypatch.setattr(body, "BODY_CONFIG_PATH", str(config))
    request = Request({"type": "http", "path": "/body", "headers": []})
    assert asyncio.run(body.body_page(request, range="90")).status_code == 200


@pytest.mark.parametrize('value', ['unknown', 'NaN', 'Infinity', True])
def test_body_table_does_not_calculate_bmi_from_invalid_values(value):
    records = [{'datestr': '2026-10-22', 'type': 'weight', 'value': value}]
    rows = body._build_table_data(records, {})
    assert not rows or ('weight' not in rows[0] and 'bmi' not in rows[0])


def test_first_body_observation_has_no_delta():
    records = [{'datestr': '2026-10-22', 'type': 'chest', 'value': 95}]
    assert body_changes(records).get('chest:2026-10-22') is None


def test_equal_observations_have_a_real_zero_delta():
    records = [{'datestr': day, 'type': 'chest', 'value': 95}
               for day in ['2026-10-22', '2026-10-21']]
    changes = body_changes(records)
    assert changes.get('chest:2026-10-21') is None
    assert changes['chest:2026-10-22'] == 0


@pytest.mark.parametrize('count,expected_label', [(1, False), (2, True)])
def test_measurement_page_only_claims_unchanged_with_a_baseline(
    count, expected_label, monkeypatch, isolated_health_db, tmp_cache, tmp_path
):
    from datetime import date, timedelta
    records = [{'datestr': (date.today() - timedelta(days=i)).isoformat(),
                'type': 'chest', 'value': 95} for i in range(count)]

    class Service:
        async def get_body(self, *args, **kwargs):
            return {'records': records}

    monkeypatch.setattr(body, 'get_data_service', Service)
    monkeypatch.setattr(body, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(body, 'BODY_CONFIG_PATH', str(tmp_path / 'body.json'))
    response = asyncio.run(body.body_page(
        Request({'type': 'http', 'path': '/body', 'headers': []}), range='90'))
    html = response.body.decode()
    assert ('较上次持平' in html) is expected_label
    if not expected_label:
        assert '无对比数据' in html


@pytest.mark.parametrize('days_apart', [0, 1])
def test_left_right_comparison_requires_same_measurement_day(
    days_apart, monkeypatch, isolated_health_db, tmp_cache, tmp_path
):
    from datetime import date, timedelta
    today = date.today().isoformat()
    left_day = (date.today() - timedelta(days=days_apart)).isoformat()

    class Service:
        async def get_body(self, *args, **kwargs):
            return {'records': [
                {'datestr': today, 'type': 'arm_right', 'value': 30},
                {'datestr': left_day, 'type': 'arm_left', 'value': 29},
            ]}

    monkeypatch.setattr(body, 'get_data_service', Service)
    monkeypatch.setattr(body, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(body, 'BODY_CONFIG_PATH', str(tmp_path / 'body.json'))
    response = asyncio.run(body.body_page(
        Request({'type': 'http', 'path': '/body', 'headers': []}), range='90'))
    html = response.body.decode()
    card = html.split('id="bodyAsymmetryCard"', 1)[1].split('<script>', 1)[0]
    assert today in card and left_day in card
    if days_apart:
        assert '日期不同，暂不比较' in card
        assert '多 1.0 cm' not in card
        assert '差异 3.4%' not in card
    else:
        assert '同日测量' in card
        assert '多 1.0 cm' in card
        assert '差异 3.4%' in card


def test_weight_history_excludes_circumference_only_dates():
    records = [
        {'datestr': '2026-10-23', 'type': 'arm_left', 'value': 29},
        {'datestr': '2026-10-22', 'type': 'weight', 'value': 70},
        {'datestr': '2026-10-21', 'type': 'bodyfat', 'value': 18},
    ]
    rows = body._build_table_data(records, body_changes(records))
    assert [row['date'] for row in rows] == ['2026-10-22', '2026-10-21']
    assert rows[0]['weight'] == 70 and rows[1]['bodyfat'] == 18


def test_body_history_preserves_year_and_observed_zero_change(
    monkeypatch, isolated_health_db, tmp_cache, tmp_path
):
    from html.parser import HTMLParser

    class Service:
        async def get_body(self, *args, **kwargs):
            return {'records': [
                {'datestr': '2026-01-01', 'type': 'weight', 'value': 70},
                {'datestr': '2025-01-01', 'type': 'weight', 'value': 70},
            ]}

    monkeypatch.setattr(body, 'get_data_service', Service)
    monkeypatch.setattr(body, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(body, 'BODY_CONFIG_PATH', str(tmp_path / 'body.json'))
    response = asyncio.run(body.body_page(
        Request({'type': 'http', 'path': '/body', 'headers': []}), range='all'))

    class Rows(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows = []
            self.row = None
            self.cell = None

        def handle_starttag(self, tag, attrs):
            if tag == 'tr' and 'body-history-row' in dict(attrs).get('class', ''):
                self.row = []
            if tag == 'td' and self.row is not None:
                self.cell = ''

        def handle_data(self, data):
            if self.cell is not None:
                self.cell += data

        def handle_endtag(self, tag):
            if tag == 'td' and self.cell is not None:
                self.row.append(self.cell.strip())
                self.cell = None
            if tag == 'tr' and self.row is not None:
                self.rows.append(self.row)
                self.row = None

    parsed = Rows()
    parsed.feed(response.body.decode())
    # CSV exports cell text, so date and delta must remain unambiguous here.
    assert [row[0] for row in parsed.rows] == ['2026-01-01', '2025-01-01']
    assert parsed.rows[0][-1] == '0.00'
    assert parsed.rows[1][-1] == '--'


@pytest.mark.parametrize('metric,unit,valid,expected', [
    ('HKQuantityTypeIdentifierBodyMass', 'kg', 70.25, '70.25'),
    ('HKQuantityTypeIdentifierBodyFatPercentage', '%', .195, '19.5'),
])
@pytest.mark.parametrize('invalid', [None, 'bad', float('inf'), float('-inf')])
def test_apple_health_body_merge_skips_bad_latest_value_and_keeps_valid_neighbor(
    isolated_health_db, metric, unit, valid, expected, invalid
):
    ah = isolated_health_db
    rows = [dict(type=metric, start_date='2026-10-22 ' + clock + ' +0800',
                 end_date='2026-10-22 ' + clock + ' +0800',
                 value=value, unit=unit, source_name='synthetic')
            for clock, value in [('08:00:00', valid), ('20:00:00', invalid)]]
    ah._bulk_insert(rows, [])
    before = ah.query_records(metric)
    merged, ah_dates = body._merge_apple_health([], '2026-10-01', '2026-10-22')
    assert merged == [{'datestr': '2026-10-22',
                       'type': 'weight' if unit == 'kg' else 'bodyfat',
                       'value': expected, 'source': 'apple_health'}]
    assert ah_dates == {'2026-10-22'}
    assert ah.query_records(metric) == before


def test_apple_health_zero_bodyfat_is_not_discarded(isolated_health_db):
    ah = isolated_health_db
    ah._bulk_insert([dict(type='HKQuantityTypeIdentifierBodyFatPercentage',
        start_date='2026-10-22 08:00:00 +0800', end_date='2026-10-22 08:00:00 +0800',
        value=0, unit='%', source_name='synthetic')], [])
    merged, dates = body._merge_apple_health([], '2026-10-01', '2026-10-22')
    assert merged == [{'datestr': '2026-10-22', 'type': 'bodyfat', 'value': '0.0', 'source': 'apple_health'}]
    assert dates == {'2026-10-22'}


def test_apple_health_percentage_overflow_does_not_hide_valid_same_day_value(isolated_health_db):
    ah = isolated_health_db
    ah._bulk_insert([dict(type='HKQuantityTypeIdentifierBodyFatPercentage',
        start_date='2026-10-22 ' + clock + ' +0800', end_date='2026-10-22 ' + clock + ' +0800',
        value=value, unit='%', source_name='synthetic')
        for clock, value in [('08:00:00', .2), ('20:00:00', 1e308)]], [])
    merged, _ = body._merge_apple_health([], '2026-10-01', '2026-10-22')
    assert merged[0]['value'] == '20.0'


def test_bad_apple_health_value_cannot_break_body_http_or_override_api(
    isolated_health_db, tmp_cache, monkeypatch, tmp_path
):
    import httpx
    from datetime import date
    from fastapi import FastAPI

    ah = isolated_health_db
    day = date.today().isoformat()
    ah._bulk_insert([dict(type='HKQuantityTypeIdentifierBodyMass',
        start_date=day + ' 08:00:00 +0800', end_date=day + ' 08:01:00 +0800',
        value='bad', unit='kg', source_name='synthetic')], [])

    class Service:
        async def get_body(self, *args):
            return {'records': [{'datestr': day, 'type': 'weight', 'value': '70'}]}

    monkeypatch.setattr(body, 'get_data_service', Service)
    monkeypatch.setattr(body, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(body, 'BODY_CONFIG_PATH', str(tmp_path / 'body.json'))
    app = FastAPI()
    app.include_router(body.router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
            response = await client.get('/body?range=all')
            assert response.status_code == 200
            assert '70' in response.text
            assert '训记' in response.text

    asyncio.run(run())
    assert ah.query_records()[0]['value'] == 'bad'
