import asyncio
from datetime import date, datetime, timezone

from routes.training import _backfill_training_history, _ensure_training_history_backfill


class FakeCache:
    def __init__(self, keys, mtimes=None):
        self.keys = keys
        self.mtimes = mtimes or {}
        self.deleted = []

    def get(self, key):
        return self.keys.get(key)

    def get_mtime(self, key):
        return self.mtimes.get(key)

    def delete(self, key):
        self.deleted.append(key)
        self.keys.pop(key, None)


class FakeDataService:
    def __init__(self):
        self.calls = []

    async def get_training(self, training_date, full=False):
        self.calls.append((training_date, full))
        return {"res": {"trains": []}}


def test_backfill_fetches_only_missing_dates_in_reverse_order():
    cache = FakeCache({"training:2026-08-02:True": {"res": {"trains": []}}})
    service = FakeDataService()

    asyncio.run(_backfill_training_history(
        service, cache, days=3, delay_seconds=0, today=date(2026, 8, 3)
    ))

    assert service.calls == [
        ("2026-08-03", True),
        ("2026-08-01", True),
    ]


def test_backfill_waits_after_each_missing_date(monkeypatch):
    import routes.training as training

    sleeps = []

    async def record_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(training.asyncio, "sleep", record_sleep)

    asyncio.run(_backfill_training_history(
        FakeDataService(), FakeCache({}), days=2, delay_seconds=6,
        today=date(2026, 8, 3),
    ))

    assert sleeps == [6, 6]


def test_backfill_refreshes_stale_empty_cache_for_recent_date():
    key = "training:2026-08-03:True"
    cache = FakeCache(
        {key: {"res": {"trains": []}}},
        {key: "2026-08-03T00:00:00+00:00"},
    )
    service = FakeDataService()

    asyncio.run(_backfill_training_history(
        service,
        cache,
        days=1,
        delay_seconds=0,
        now=datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc),
    ))

    assert service.calls == [("2026-08-03", True)]
    assert cache.deleted == [key]


def test_ensure_backfill_does_not_create_second_live_task(monkeypatch):
    import routes.training as training

    class LiveTask:
        def done(self):
            return False

    def fail_if_called(coro):
        coro.close()
        raise AssertionError("must not create a duplicate task")

    monkeypatch.setattr(training, "_history_backfill_task", LiveTask())
    monkeypatch.setattr(training.asyncio, "create_task", fail_if_called)

    _ensure_training_history_backfill(FakeDataService(), FakeCache({}))
