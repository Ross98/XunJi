import asyncio
import sys; sys.path.insert(0, '.')
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks
from starlette.requests import Request

from routes.dashboard import (
    _extract_trains,
    _body_cache_needs_refresh,
    _get_body_records,
    _get_cached_training_dates,
    _get_daily_health_total,
    _get_training_or_empty,
    _refresh_body_cache,
)
import routes.dashboard as dashboard_module


def test_week_change_does_not_compare_sparse_samples_as_seven_days():
    trend = dashboard_module._build_trend([
        {"start_date": f"2026-08-{day:02d}", "value": value}
        for day, value in [(1, 38.6), (3, 38.9), (4, 39.1), (8, 39.2),
                           (15, 38.6), (24, 38.4), (25, 38.4), (30, 38.0)]
    ])
    assert dashboard_module._chg7(trend) is None
    assert not any(value is not None for value in dashboard_module._ma7(trend))


def test_week_change_and_average_follow_calendar_across_year_boundary():
    from datetime import date, timedelta
    trend = dashboard_module._build_trend([
        {"start_date": (date(2025, 12, 28) + timedelta(days=i)).isoformat(), "value": i}
        for i in range(8)
    ])
    assert dashboard_module._chg7(trend) == 7
    assert dashboard_module._ma7(trend)[-1] == 4


def test_extract_trains_returns_empty_list_for_string_error_response():
    data = {"success": False, "res": "too frequent, retry after 5s"}

    assert _extract_trains(data) == []


def test_extract_trains_supports_nested_and_top_level_payloads():
    nested = [{"datestr": "2026-07-26"}]
    top_level = [{"datestr": "2026-07-25"}]

    assert _extract_trains({"res": {"trains": nested}}) == nested
    assert _extract_trains({"trains": top_level}) == top_level


def test_get_training_or_empty_tolerates_upstream_failure():
    class FailingDataService:
        async def get_training(self, date):
            raise RuntimeError("502 Bad Gateway")

    result = asyncio.run(
        _get_training_or_empty(FailingDataService(), "2026-07-28")
    )

    assert result == {"res": {"trains": []}}


def test_refresh_body_cache_forces_upstream_fetch():
    class DataService:
        def __init__(self):
            self.calls = []

        async def get_body(self, start, end, force_refresh=False):
            self.calls.append((start, end, force_refresh))
            return {"records": []}

    service = DataService()
    asyncio.run(_refresh_body_cache(service, "2026-01-01", "2026-01-31"))

    assert service.calls == [("2026-01-01", "2026-01-31", True)]


def test_body_cache_refresh_is_throttled_for_fifteen_minutes():
    class Cache:
        def __init__(self, mtime):
            self.mtime = mtime

        def get_mtime(self, key):
            return self.mtime

    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)

    assert not _body_cache_needs_refresh(
        Cache("2026-08-09T11:50:00+00:00"), "body:key", now
    )
    assert _body_cache_needs_refresh(
        Cache("2026-08-09T11:30:00+00:00"), "body:key", now
    )


def test_body_records_fall_back_to_empty_when_cold_api_fails():
    class EmptyCache:
        def get(self, key):
            return None

    class FailingDataService:
        async def get_body(self, start, end):
            raise RuntimeError("upstream unavailable")

    records = asyncio.run(_get_body_records(
        FailingDataService(), EmptyCache(), "body:key", "2026-01-01", "2026-01-31"
    ))

    assert records == []


def test_recovery_training_dates_use_cache_without_api_calls():
    class Cache:
        values = {
            "training:2026-08-09:False": {"res": {"trains": [{"datestr": "2026-08-09"}]}},
            "training:2026-08-08:True": {"trains": [{"datestr": "2026-08-08"}]},
        }

        def get(self, key):
            return self.values.get(key)

    assert _get_cached_training_dates(Cache(), "2026-08-09", days=3) == {
        "2026-08-08", "2026-08-09"
    }


def test_daily_health_total_reports_actual_fallback_date():
    class AppleHealth:
        @staticmethod
        def query_records(metric, start, end):
            if start == "2026-08-08":
                return []
            return [{"value": 40}, {"value": 60}]

        @staticmethod
        def get_latest_dates(metric):
            return {metric: "2026-08-07"}

    assert _get_daily_health_total(
        AppleHealth(), "steps", "2026-08-08"
    ) == (100, "2026-08-07")


def test_dashboard_renders_without_retired_diet_service(monkeypatch):
    class DataService:
        async def get_training(self, training_date):
            return {
                "res": {
                    "trains": [{
                        "datestr": training_date,
                        "movements": [{
                            "name": "卧推",
                            "sets": [{"weight": 60}, {"weight": 65}],
                        }],
                    }]
                }
            }

        async def get_body(self, start, end):
            return {"res": {"records": []}}

    class Cache:
        def get(self, key):
            return None

    class AppleHealth:
        HEALTH_DB_PATH = Path("/definitely/not/imported.sqlite")

    class Freshness:
        def __init__(self, **kwargs):
            pass

        def get_freshness_context(self):
            return {"ah_status": "none"}

    monkeypatch.setattr(dashboard_module, "get_data_service", DataService)
    monkeypatch.setattr(dashboard_module, "get_cache", Cache)
    monkeypatch.setattr(dashboard_module, "_get_ah", lambda: AppleHealth())
    monkeypatch.setattr(dashboard_module, "DataFreshnessService", Freshness)

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "server": ("test", 80),
        "client": ("test", 123),
        "scheme": "http",
        "root_path": "",
    })
    response = asyncio.run(
        dashboard_module.dashboard_content(request, BackgroundTasks())
    )
    html = response.body.decode()

    assert "2组" in html
    assert "昨日活动能量 (kcal)" in html
    assert "<!DOCTYPE html>" not in html


def test_dashboard_loader_does_not_touch_data_services(monkeypatch):
    def fail_if_called():
        raise AssertionError("loader must render before dashboard data is fetched")

    monkeypatch.setattr(dashboard_module, "get_data_service", fail_if_called)
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "server": ("test", 80),
        "client": ("test", 123),
        "scheme": "http",
        "root_path": "",
    })

    response = asyncio.run(dashboard_module.dashboard_loader(request))
    html = response.body.decode()

    assert 'role="progressbar"' in html
    assert "/dashboard/content" in html
    assert "重新加载" in html
