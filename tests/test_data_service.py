import sys; sys.path.insert(0, '.')
import asyncio
from data_service import DataFreshnessService, DataService


class FakeCache:
    def __init__(self, value=None):
        self.value = value
        self.set_calls = []
        self.delete_calls = []

    def get(self, key):
        return self.value

    def set(self, key, value):
        self.set_calls.append((key, value))

    def delete(self, key):
        self.delete_calls.append(key)


class BodyClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def query_body(self, start, end, types, limit):
        self.calls.append((start, end, types, limit))
        return self.response


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def fetch_training(self, date, full):
        self.calls.append((date, full))
        return self.response


def test_get_training_evicts_invalid_cache_and_does_not_cache_api_error():
    invalid = {"success": False, "res": "too frequent, retry after 5s"}
    cache = FakeCache(invalid)
    client = FakeClient(invalid)
    service = DataService(cache, client)

    result = asyncio.run(service.get_training("2026-07-26"))

    assert result == invalid
    assert cache.delete_calls == ["training:2026-07-26:False"]
    assert client.calls == [("2026-07-26", False)]
    assert cache.set_calls == []


def test_get_body_force_refresh_bypasses_cached_value():
    cache = FakeCache({"records": [{"value": "old"}]})
    client = BodyClient({"records": [{"value": "fresh"}]})
    service = DataService(cache, client)

    result = asyncio.run(service.get_body(
        "2026-01-01", "2026-01-31", force_refresh=True
    ))

    assert result == {"records": [{"value": "fresh"}]}
    assert client.calls == [("2026-01-01", "2026-01-31", None, 500)]
    assert cache.set_calls[0] == (
        "body:all:2026-01-01:2026-01-31:500",
        {"records": [{"value": "fresh"}]},
    )


def test_get_body_evicts_application_error_and_does_not_cache_it():
    invalid = {"success": False, "res": "too frequent"}
    cache = FakeCache(invalid)
    client = BodyClient(invalid)
    service = DataService(cache, client)

    result = asyncio.run(service.get_body("2026-01-01", "2026-01-31"))

    assert result == invalid
    assert cache.delete_calls == ["body:all:2026-01-01:2026-01-31:500"]
    assert cache.set_calls == []

def test_freshness_context_has_required_keys():
    class EmptyHealth:
        @staticmethod
        def get_latest_dates():
            return {}

    class RefreshCache:
        @staticmethod
        def get(key):
            assert key == "_xunji_refresh_time"
            return {"time": "2026-08-09T10:30:00+00:00"}

    svc = DataFreshnessService(EmptyHealth(), RefreshCache())
    ctx = svc.get_freshness_context()
    assert "ah_overall_latest" in ctx
    assert "xunji_latest" in ctx
    assert "ah_days_ago" in ctx
    assert "ah_status" in ctx
    assert "metrics" in ctx
    assert isinstance(ctx["metrics"], dict)
    assert ctx["xunji_latest"] == "2026-08-09T10:30:00+00:00"
