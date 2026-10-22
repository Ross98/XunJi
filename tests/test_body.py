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
