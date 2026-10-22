import json
import pytest

def sample(identifier="one", kind="quantity", **changes):
    row = dict(schemaVersion=1, id=identifier, kind=kind,
               type="HKQuantityTypeIdentifierStepCount",
               startDate="2026-09-29T16:00:00.125Z",
               endDate="2026-09-29T16:01:00.125Z",
               source={"name": "Watch"}, quantity={"value": 100, "unit": "count"})
    row.update(changes)
    return row


def batch(folder, name, rows):
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


@pytest.mark.parametrize("bad_record", [
    sample(source=[]), sample(quantity=[]), sample(startDate=123),
    dict(schemaVersion=1, kind="typeCoverage", type="HeartRate", complete=True,
         observedAt="2026-09-30T02:00:00"),
])
def test_malformed_batch_does_not_block_other_files(isolated_health_db, tmp_path, bad_record):
    import hozz_sync as hs
    folder = tmp_path / "batches"
    batch(folder, "a.ndjson", [sample("rolled-back"), bad_record])
    batch(folder, "b.ndjson", [sample("valid")])
    result = hs.sync(folder)
    assert result["status"] == "partial"
    assert result["new_samples"] == 1
    assert result["files"] == 1
    assert len(result["errors"]) == 1
    assert hs.status()["samples"] == 1


def test_import_repeat_timezone_sleep_and_coverage(isolated_health_db, tmp_path):
    import hozz_sync as hs
    folder = tmp_path / "batches"
    batch(folder, "a.ndjson", [sample(), sample(), sample("sleep", "category",
          type="HKCategoryTypeIdentifierSleepAnalysis", value=4),
          dict(schemaVersion=1, kind="typeCoverage", type="HeartRate", complete=False,
               state="draining", observedAt="2026-09-30T01:00:00Z")])
    result = hs.sync(folder)
    assert result["new_samples"] == 2
    assert result["inserted"] == 2
    assert hs.sync(folder)["inserted"] == 0
    ah = isolated_health_db
    assert ah.query_records()[0]["start_date"] == "2026-09-30 00:00:00 +0800"
    assert ah.query_sleep()[0]["type"] == "HKCategoryValueSleepAnalysisAsleepDeep"
    assert hs.status()["incomplete"] == 1


def test_legacy_overlap_not_counted_twice(isolated_health_db, tmp_path):
    import hozz_sync as hs
    ah = isolated_health_db
    ah._bulk_insert([dict(type="HKQuantityTypeIdentifierStepCount",
        start_date="2026-09-30 00:00:00 +0800", end_date="2026-09-30 00:01:00 +0800",
        value=100, unit="count", source_name="Watch")], [])
    batch(tmp_path / "batches", "a.ndjson", [sample()])
    result = hs.sync(tmp_path / "batches")
    assert result["inserted"] == 0
    assert len(ah.query_records()) == 1


def test_bad_file_rolls_back_and_can_retry(isolated_health_db, tmp_path):
    import hozz_sync as hs
    folder = tmp_path / "batches"
    path = batch(folder, "a.ndjson", [sample()])
    path.write_text(path.read_text() + "{broken\n")
    assert hs.sync(folder)["errors"]
    assert isolated_health_db.query_records() == []
    batch(folder, "a.ndjson", [sample()])
    assert hs.sync(folder)["inserted"] == 1


def test_tombstone_prevents_late_resurrection(isolated_health_db, tmp_path):
    import hozz_sync as hs
    folder = tmp_path / "batches"
    batch(folder, "a.ndjson", [sample()])
    hs.sync(folder)
    batch(folder, "b.ndjson", [dict(schemaVersion=1, kind="deletion", id="one",
          type="HKQuantityTypeIdentifierStepCount")])
    assert hs.sync(folder)["deleted"] == 1
    batch(folder, "c.ndjson", [sample()])
    hs.sync(folder)
    assert isolated_health_db.query_records() == []


def test_unknown_records_preserved_coverage_does_not_regress(isolated_health_db, tmp_path):
    import hozz_sync as hs
    folder = tmp_path / "batches"
    batch(folder, "a.ndjson", [sample("ecg", "ecg"),
          dict(schemaVersion=1, kind="typeCoverage", type="HeartRate", complete=True,
               state="anchorClosed", observedAt="2026-09-30T02:00:00Z")])
    hs.sync(folder)
    batch(folder, "b.ndjson", [dict(schemaVersion=1, kind="typeCoverage", type="HeartRate",
          complete=False, state="draining", observedAt="2026-09-30T01:00:00Z")])
    hs.sync(folder)
    assert hs.status()["complete"] == 1
    assert hs.status()["unprojected"] == 1


def test_workout_and_unit_conversion(isolated_health_db, tmp_path):
    import hozz_sync as hs
    folder = tmp_path / "batches"
    batch(folder, "a.ndjson", [sample(quantity={"value": 60, "unit": "s"},
          type="HKQuantityTypeIdentifierAppleStandTime"),
          sample("run", "workout", type="HKWorkoutTypeIdentifier", activityType=37,
                 duration=1800)])
    hs.sync(folder)
    assert isolated_health_db.query_records()[0]["value"] == 1
    assert isolated_health_db.query_records()[0]["unit"] == "min"
    assert isolated_health_db.query_workouts()[0]["workout_activity_type"] == "HKWorkoutActivityTypeRunning"
    assert isolated_health_db.query_workouts()[0]["duration"] == 30


def test_changed_file_reprocessed_and_missing_folder_explicit(isolated_health_db, tmp_path):
    import hozz_sync as hs
    assert hs.sync(tmp_path / "absent")["status"] == "unavailable"
    folder = tmp_path / "batches"
    batch(folder, "a.ndjson", [sample()])
    hs.sync(folder)
    batch(folder, "a.ndjson", [sample(), sample("two", startDate="2026-09-30T02:00:00Z")])
    assert hs.sync(folder)["new_samples"] == 1


def test_same_timestamp_sources_preserved_with_collision_report(isolated_health_db, tmp_path):
    import hozz_sync as hs
    folder = tmp_path / "batches"
    batch(folder, "a.ndjson", [sample(), sample("two", source={"name": "Phone"})])
    hs.sync(folder)
    assert hs.status()["samples"] == 2
    assert hs.status()["unprojected"] == 1


def test_status_uses_current_folder_and_marks_old_success_stale(isolated_health_db, tmp_path, monkeypatch):
    import hozz_sync as hs
    old_folder = tmp_path / "previous-user"
    old_folder.mkdir()
    hs.sync(old_folder)
    with isolated_health_db._conn() as conn:
        row = json.loads(conn.execute("SELECT raw FROM hozz_sync_state WHERE id=1").fetchone()[0])
        row["checked_at"] = "2026-01-01T00:00:00+00:00"
        conn.execute("UPDATE hozz_sync_state SET raw=? WHERE id=1", (json.dumps(row),))
    current_folder = tmp_path / "current-user"
    monkeypatch.setattr(hs, "source_folder", lambda: current_folder)
    result = hs.status()
    assert result["folder"] == str(current_folder)
    assert result["last_folder"] == str(old_folder)
    assert result["check_stale"] is True
    assert result["checked_at_bj"] == "2026-01-01 08:00:00 +0800"


def test_status_fresh_check_and_never_checked_state(isolated_health_db, tmp_path):
    import hozz_sync as hs
    assert hs.status()["check_stale"] is True
    folder = tmp_path / "batches"
    folder.mkdir()
    hs.sync(folder)
    assert hs.status()["check_stale"] is False
