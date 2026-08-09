import asyncio

from routes import health


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
