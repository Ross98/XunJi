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
