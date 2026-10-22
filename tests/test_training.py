import asyncio
from copy import deepcopy
from datetime import date, datetime, timezone

from routes.training import (
    _merge_overlapping_trains,
    _backfill_training_history,
    _ensure_training_history_backfill,
    stop_training_background_tasks,
    compute_frequency_stats,
)


def session(start, end, kind="strength"):
    return {"start": start * 60000, "end": end * 60000,
            "movements": [{"name": kind, "exetype": kind, "sets": []}]}


def test_nearby_same_kind_sessions_are_not_dropped():
    for kind in ("strength", "cardio"):
        trains = [session(0, 30, kind), session(35, 60, kind)]
        assert len(_merge_overlapping_trains(trains)) == 2


def test_merge_does_not_mutate_input_and_updates_duration():
    trains = [session(0, 30), session(5, 35, "cardio"), session(36, 60, "cardio")]
    original = deepcopy(trains)
    merged = _merge_overlapping_trains(trains)
    assert trains == original
    assert len(merged) == 1
    assert merged[0]["_cardio_metrics"]["total_duration_min"] == 60


def test_merge_keeps_sessions_without_timing_evidence_or_on_different_dates():
    from routes.training import _merge_overlapping_trains

    manual, cardio = session(0, 30), session(5, 35, "cardio")
    manual["datestr"], cardio["datestr"] = "2026-10-21", "2026-10-22"
    assert len(_merge_overlapping_trains([manual, cardio])) == 2
    for train in (manual, cardio):
        train.pop("start")
        train.pop("end")
        train["datestr"] = "2026-10-22"
    assert len(_merge_overlapping_trains([manual, cardio])) == 2


def test_training_detail_tolerates_application_error(monkeypatch, tmp_cache):
    from starlette.requests import Request
    from routes import training

    class Service:
        async def get_training(self, *args, **kwargs):
            return {"success": False, "res": "too frequent"}

    monkeypatch.setattr(training, "get_data_service", Service)
    monkeypatch.setattr(training, "get_cache", lambda: tmp_cache)
    tmp_cache.set("training:2026-08-01:True", {"res": "too frequent"})
    request = Request({"type": "http", "path": "/training/2026-08-01"})
    response = asyncio.run(training.training_detail_fragment(request, "2026-08-01"))
    assert response.status_code == 200


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


def test_frequency_stats_count_calendar_gaps_in_streaks():
    stats = compute_frequency_stats(
        {
            "2026-08-01": 1,
            "2026-08-02": 2,
            "2026-08-04": 1,
            "2026-08-08": 1,
        },
        today=date(2026, 8, 9),
    )

    assert stats == {
        "total": 5,
        "days": 4,
        "longest_streak": 2,
        "current_streak": 1,
        "weekly_average": 0.4,
        "rest_days": 86,
    }


def test_frequency_stats_current_streak_expires_after_full_rest_day():
    stats = compute_frequency_stats(
        {"2026-08-06": 1, "2026-08-07": 1},
        today=date(2026, 8, 9),
    )

    assert stats["longest_streak"] == 2
    assert stats["current_streak"] == 0


def test_stop_training_background_tasks_cancels_live_task(monkeypatch):
    import routes.training as training

    async def run():
        task = asyncio.create_task(asyncio.sleep(60))
        monkeypatch.setattr(training, "_history_backfill_task", task)
        await stop_training_background_tasks()
        return task

    task = asyncio.run(run())

    assert task.cancelled()
    assert training._history_backfill_task is None


def test_nested_sets_are_visible_in_the_detail_fragment(monkeypatch, tmp_cache):
    import re
    from starlette.requests import Request
    from routes import training

    class Service:
        async def get_training(self, *args, **kwargs):
            return {'trains': [{'datestr': '2026-10-21', 'movements': [
                {'name': '卧推', 'sets': [{'items': [
                    {'set': {'weight': '60', 'unit': 'kg', 'reps': '10'}},
                    {'set': {'weight': '50', 'unit': 'kg', 'reps': '8'}},
                ]}]},
            ]}]}

    monkeypatch.setattr(training, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(training, 'get_data_service', Service)
    response = asyncio.run(training.training_detail_fragment(
        Request({'type': 'http', 'path': '/training/2026-10-21', 'headers': []}), '2026-10-21'))
    html = re.sub(r'\s+', ' ', response.body.decode())
    assert '60kg × 10次' in html
    assert '50kg × 8次' in html
    assert '第2组' in html


def test_cardio_only_today_renders_in_full_training_page(monkeypatch, tmp_cache):
    from starlette.requests import Request
    from routes import training
    today = date.today().isoformat()
    payload = {'trains': [{'datestr': today, 'movements': [
        {'name': '跑步', 'exetype': 'cardio', 'sets': [{'time': 1200, 'calories': 100}]},
    ]}]}

    class Service:
        async def get_training(self, day, full=False):
            return payload if day == today else {'trains': []}

    tmp_cache.set(f'training:{today}:True', payload)
    monkeypatch.setattr(training, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(training, 'get_data_service', Service)
    monkeypatch.setattr(training, '_ensure_training_history_backfill', lambda *args: None)
    response = asyncio.run(training.training_page(
        Request({'type': 'http', 'path': '/training', 'headers': []})))
    assert response.status_code == 200
    html = response.body.decode()
    assert '有氧分钟' in html
    assert 'stat-value">20<' in html
    assert 'stat-value">100<' in html


def test_movement_tracker_accepts_encoded_slashes(monkeypatch, tmp_cache):
    import httpx
    from urllib.parse import quote
    from app import app
    from routes import training
    name = '卧推 / 哑铃'
    day = '2026-10-21'
    tmp_cache.set(f'training:{day}:True', {'trains': [{'datestr': day, 'movements': [
        {'name': name, 'sets': [{'weight': 60, 'reps': 10}]},
    ]}]})
    monkeypatch.setattr(training, 'get_cache', lambda: tmp_cache)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/training/track/' + quote(name, safe=''))
        assert response.status_code == 200
        assert response.json()['dates'] == [day]
        assert response.json()['total_volume'] == [600]
    asyncio.run(run())
