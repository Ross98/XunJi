import asyncio

import pytest
from pathlib import Path

from routes import health


def test_body_table_keeps_latest_measurement_per_day():
    weights = [{"start_date": "2026-10-22 20:00:00", "value": 71},
               {"start_date": "2026-10-22 08:00:00", "value": 70}]
    fats = [{"start_date": "2026-10-22 19:00:00", "value": 0.19},
            {"start_date": "2026-10-22 07:00:00", "value": 0.20}]
    assert health._build_body_table(weights, fats) == [
        {"date": "2026-10-22", "weight": 71, "bodyfat": 19}]


def test_health_page_only_reloads_after_observing_active_import():
    template = (Path(__file__).parents[1] / "templates" / "health.html").read_text(
        encoding="utf-8"
    )

    assert "let importWasRunning = false;" in template
    assert "if (state.status === 'running') importWasRunning = true;" in template
    assert "state.status === 'ok' && importWasRunning" in template


def test_health_import_starts_background_job(monkeypatch):
    calls = []
    cache = object()

    def fake_import_all(*, cache, progress_callback=None, cancel_event=None):
        calls.append(("import", cache))
        if progress_callback:
            progress_callback(21)
        return 42

    monkeypatch.setattr(health.ah, "import_all", fake_import_all)
    monkeypatch.setattr(health, "get_cache", lambda: cache)
    health._reset_import_state_for_tests()

    async def run():
        result = await health.trigger_import()
        assert result["status"] == "started"
        await health._import_task
        return await health.import_status()

    status = asyncio.run(run())

    assert calls == [("import", cache)]
    assert status["status"] == "ok"
    assert status["total"] == 42


def test_health_import_rejects_duplicate_run():
    health._reset_import_state_for_tests(status="running")
    result = asyncio.run(health.trigger_import())

    assert result == {
        "status": "busy",
        "message": "Apple Health 数据正在导入",
    }


def test_health_labels_are_localized():
    assert health.health_label("HKQuantityTypeIdentifierHeartRate") == "心率"
    assert health.health_label("HKQuantityTypeIdentifierWalkingAsymmetryPercentage") == "步态不对称"
    assert health.health_label("HKQuantityTypeIdentifierAppleWalkingSteadiness") == "步行稳定性"
    assert health.workout_label("HKWorkoutActivityTypeWalking") == "步行"


def test_stop_health_background_tasks_signals_and_cancels(monkeypatch):
    async def run():
        task = asyncio.create_task(asyncio.sleep(60))
        monkeypatch.setattr(health, "_import_task", task)
        health._import_cancel_event.clear()
        await health.stop_health_background_tasks()
        return task

    task = asyncio.run(run())

    assert health._import_cancel_event.is_set()
    assert task.cancelled()
    assert health._import_task is None


def test_hozz_manual_sync_starts_job_and_reports_result(monkeypatch):
    calls = []
    monkeypatch.setattr(health.hs, "sync", lambda **kwargs: calls.append(kwargs) or
        {"status": "ok", "inserted": 12, "errors": []})
    monkeypatch.setattr(health, "get_cache", lambda: "cache")
    health._reset_import_state_for_tests()

    async def run():
        assert (await health.trigger_hozz_sync())["status"] == "started"
        await health._import_task
        assert (await health.import_status())["total"] == 12
    asyncio.run(run())
    assert calls[0]["cache"] == "cache"


def test_auto_sync_runs_then_stops_with_project(monkeypatch):
    calls = []
    async def fake_trigger():
        calls.append("sync")
        return {"status": "started"}
    monkeypatch.setattr(health, "trigger_hozz_sync", fake_trigger)
    health._reset_import_state_for_tests()

    async def run():
        await health.start_health_background_tasks()
        await asyncio.sleep(0)
        assert calls == ["sync"]
        task = health._sync_watch_task
        await health.stop_health_background_tasks()
        assert task.cancelled()
    asyncio.run(run())


def test_auto_sync_recovers_after_trigger_failure(monkeypatch):
    calls = []

    async def run():
        second_check = asyncio.Event()

        async def fake_trigger():
            calls.append("check")
            if len(calls) == 1:
                raise RuntimeError("temporary failure")
            second_check.set()
            return {"status": "started"}

        monkeypatch.setattr(health, "trigger_hozz_sync", fake_trigger)
        monkeypatch.setattr(health, "SYNC_INTERVAL_SECONDS", 0)
        await health.start_health_background_tasks()
        try:
            await asyncio.wait_for(second_check.wait(), 1)
        finally:
            await health.stop_health_background_tasks()

    asyncio.run(run())
    assert len(calls) >= 2


def test_hozz_status_includes_live_watcher_state(monkeypatch):
    monkeypatch.setattr(health.hs, "status", lambda: {"status": "ok"})
    monkeypatch.setattr(health, "_sync_watch_task", None)
    assert asyncio.run(health.hozz_sync_status())["watcher_running"] is False

    class LiveTask:
        def done(self):
            return False

    monkeypatch.setattr(health, "_sync_watch_task", LiveTask())
    assert asyncio.run(health.hozz_sync_status())["watcher_running"] is True


def test_sleep_only_store_is_imported_and_shown(monkeypatch, isolated_health_db, tmp_cache):
    from datetime import date
    from starlette.requests import Request
    import hozz_sync
    ah = isolated_health_db
    day = date.today().isoformat()
    with ah._conn() as conn:
        conn.execute('INSERT INTO health_sleep (type,start_date,end_date) VALUES (?,?,?)',
                     ('HKCategoryValueSleepAnalysisAsleepCore', day + ' 01:00:00 +0800', day + ' 07:00:00 +0800'))
    monkeypatch.setattr(health, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(hozz_sync, 'source_folder', lambda: Path('/not-configured'))
    response = asyncio.run(health.health_page(Request({'type': 'http', 'path': '/health', 'headers': []})))
    html = response.body.decode()
    assert ah.get_import_count()['total_sleep'] == 1
    assert '尚未导入 Apple Health' not in html
    assert '最近睡眠汇总' in html
    assert '6.0小时' in html


def test_cancelled_hozz_job_does_not_report_completion(monkeypatch):
    monkeypatch.setattr(health.hs, 'sync', lambda **kwargs:
                        {'status': 'cancelled', 'inserted': 0, 'errors': []})
    monkeypatch.setattr(health, 'get_cache', lambda: object())
    health._reset_import_state_for_tests()
    asyncio.run(health._run_hozz_job())
    result = asyncio.run(health.import_status())
    assert result['status'] == 'cancelled'
    assert '已停止' in result['message']
    assert '完成' not in result['message']


@pytest.mark.parametrize("kind", ["hozz", "xml"])
@pytest.mark.parametrize("fail", [False, True])
def test_shutdown_waits_for_import_worker_thread_before_returning(monkeypatch, kind, fail):
    import threading

    async def check():
        entered = threading.Event()
        cancel_seen = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def worker(*, cancel_event, **kwargs):
            entered.set()
            try:
                if cancel_event.wait(2):
                    cancel_seen.set()
                assert release.wait(2), 'test did not release worker'
                if fail:
                    raise RuntimeError('worker failed during cancellation')
                return ({'status': 'cancelled', 'inserted': 0, 'errors': []}
                        if kind == 'hozz' else 0)
            finally:
                finished.set()

        health._reset_import_state_for_tests()
        monkeypatch.setattr(health, 'get_cache', lambda: object())
        monkeypatch.setattr(health.hs if kind == 'hozz' else health.ah,
                            'sync' if kind == 'hozz' else 'import_all', worker)
        trigger = health.trigger_hozz_sync if kind == 'hozz' else health.trigger_import
        stop = None
        try:
            await trigger()
            job = health._import_task
            assert await asyncio.to_thread(entered.wait, 1)
            stop = asyncio.create_task(health.stop_health_background_tasks())
            assert await asyncio.to_thread(cancel_seen.wait, 1)
            # Allow cancellation to propagate while the real worker still owns resources.
            await asyncio.sleep(0.02)
            assert not stop.done(), 'shutdown returned before its worker released resources'
            assert not finished.is_set()
            job.cancel()
            await asyncio.sleep(0.02)
            assert not stop.done(), 'repeated cancellation abandoned worker'
        finally:
            release.set()
            if stop is not None:
                await stop
            assert await asyncio.to_thread(finished.wait, 1)
        assert health._import_task is None
        assert (await health.import_status())['status'] == 'cancelled'

    asyncio.run(check())
