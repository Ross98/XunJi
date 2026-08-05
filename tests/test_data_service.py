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

def test_freshness_context_has_required_keys():
    svc = DataFreshnessService()
    ctx = svc.get_freshness_context()
    assert "ah_overall_latest" in ctx
    assert "xunji_latest" in ctx
    assert "ah_days_ago" in ctx
    assert "ah_status" in ctx
    assert "metrics" in ctx
    assert isinstance(ctx["metrics"], dict)
