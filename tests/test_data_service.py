import sys; sys.path.insert(0, '.')
import asyncio
import pytest
from data_service import DataFreshnessService, DataService, extract_body_records, extract_trains, get_cached_training


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


@pytest.mark.parametrize("nested", [True, False])
def test_extractors_preserve_supported_payload_shapes(nested):
    rows = [{"datestr": "2026-10-22", "type": "weight", "value": "70"}]
    payload = {"records": '[{"datestr":"2026-10-22","type":"weight","value":"70"}]'}
    assert extract_body_records({"res": payload} if nested else payload) == rows
    payload = {"trains": rows}
    assert extract_trains({"res": payload} if nested else payload) == rows


def test_valid_empty_response_remains_cacheable():
    cache = FakeCache()
    service = DataService(cache, FakeClient({"res": {"trains": []}}))
    asyncio.run(service.get_training("2026-01-01"))
    assert cache.set_calls[0] == ("training:2026-01-01:False", {"res": {"trains": []}})


def test_training_uses_newer_summary_when_old_full_cache_is_empty(tmp_cache):
    day = "2026-09-04"
    empty = {"res": {"trains": []}}
    populated = {"res": {"trains": [{"datestr": day, "movements": []}]}}
    tmp_cache.set(f"training:{day}:True", empty)
    tmp_cache.set(f"training:{day}:False", populated)
    assert get_cached_training(tmp_cache, day, full=True) == populated


def test_newer_empty_full_cache_remains_authoritative(tmp_cache):
    day = "2026-09-04"
    empty = {"res": {"trains": []}}
    tmp_cache.set(f"training:{day}:False", {"res": {"trains": [{"datestr": day}]}})
    tmp_cache.set(f"training:{day}:True", empty)
    service = DataService(tmp_cache, FakeClient(empty))
    assert asyncio.run(service.get_training(day, full=True)) == empty


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


@pytest.mark.parametrize("payload", [
    {"res": {}}, {"res": {"records": "{broken"}},
    {"res": {"records": None}}, {"res": {"records": [None]}},
    {"success": False, "res": {"records": []}},
])
def test_get_body_does_not_cache_malformed_payload(payload):
    cache = FakeCache()
    service = DataService(cache, BodyClient(payload))
    asyncio.run(service.get_body("2026-01-01", "2026-01-31"))
    assert cache.set_calls == []


@pytest.mark.parametrize("payload", [
    {"res": {}}, {"res": {"trains": [None]}},
    {"success": False, "res": {"trains": []}},
])
def test_get_training_does_not_cache_malformed_payload(payload):
    cache = FakeCache()
    service = DataService(cache, FakeClient(payload))
    asyncio.run(service.get_training("2026-01-01"))
    assert cache.set_calls == []


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

        @staticmethod
        def get_data_coverage():
            return {"start_date": None, "end_date": None}

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


def test_corrupt_training_cache_is_replaced_from_upstream(tmp_cache):
    key = 'training:2026-10-22:False'
    with tmp_cache._conn() as conn:
        conn.execute('INSERT INTO cache VALUES (?,?,?)', (key, '{broken', '2026-10-22'))
    response = {'res': {'trains': []}}
    client = FakeClient(response)
    result = asyncio.run(DataService(tmp_cache, client).get_training('2026-10-22'))
    assert result == response
    assert client.calls == [('2026-10-22', False)]
    assert tmp_cache.get(key) == response


def test_overall_health_freshness_includes_workouts_and_sleep(isolated_health_db):
    from datetime import date, timedelta
    ah = isolated_health_db
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with ah._conn() as conn:
        conn.execute('INSERT INTO health_sleep (type,start_date,end_date) VALUES (?,?,?)',
                     ('HKCategoryValueSleepAnalysisAsleepCore', yesterday + ' 23:00:00 +0800',
                      date.today().isoformat() + ' 07:00:00 +0800'))
    result = DataFreshnessService(ah).get_freshness_context()
    assert result['ah_overall_latest'] == yesterday
    assert result['ah_status'] == 'fresh'
    assert result['metrics']['hrv']['status'] == 'none'


def test_newer_workout_advances_overall_freshness_only(isolated_health_db):
    from datetime import date, timedelta
    ah = isolated_health_db
    older = (date.today() - timedelta(days=10)).isoformat()
    today = date.today().isoformat()
    with ah._conn() as conn:
        conn.execute('INSERT INTO health_records (type,start_date,end_date,value) VALUES (?,?,?,?)',
                     ('HKQuantityTypeIdentifierHeartRateVariabilitySDNN', older + ' 08:00:00 +0800',
                      older + ' 08:01:00 +0800', 40))
        conn.execute('INSERT INTO health_workouts (workout_activity_type,start_date,end_date) VALUES (?,?,?)',
                     ('Walking', today + ' 08:00:00 +0800', today + ' 08:30:00 +0800'))
    result = DataFreshnessService(ah).get_freshness_context()
    assert result['ah_overall_latest'] == today
    assert result['ah_status'] == 'fresh'
    assert result['metrics']['hrv']['latest'] == older
    assert result['metrics']['hrv']['status'] == 'expired'


@pytest.mark.parametrize('bad_value', ['unknown', 'NaN', 'Infinity', [], {}, True, None])
def test_body_extractor_skips_invalid_measurements_without_losing_valid_rows(bad_value):
    good = {'datestr': '2026-10-21', 'type': 'weight', 'value': '70'}
    bad = {'datestr': '2026-10-22', 'type': 'weight', 'value': bad_value}
    payload = {'res': {'records': [bad, good]}}
    assert extract_body_records(payload) == [good]
    assert payload['res']['records'] == [bad, good]


@pytest.mark.parametrize('changes', [{'datestr': None}, {'datestr': 'bad-date'}, {'type': []}])
def test_body_extractor_rejects_unusable_record_identity(changes):
    bad = {'datestr': '2026-10-22', 'type': 'weight', 'value': '70', **changes}
    assert extract_body_records({'records': [bad]}) == []
