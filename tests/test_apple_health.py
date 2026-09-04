import sys; sys.path.insert(0, '.')

import threading

import pytest

def test_get_latest_dates_returns_dict(isolated_health_db):
    ah = isolated_health_db
    result = ah.get_latest_dates()
    assert isinstance(result, dict)
    assert all(isinstance(k, str) for k in result)
    assert all(isinstance(v, str) or v is None for v in result.values())

def test_get_latest_dates_filtered(isolated_health_db):
    ah = isolated_health_db
    with ah._conn() as conn:
        conn.execute(
            """INSERT INTO health_records
               (type, start_date, end_date, value, unit, source_name)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "HKQuantityTypeIdentifierStepCount",
                "2026-08-08 12:00:00 +0800",
                "2026-08-08 12:01:00 +0800",
                100,
                "count",
                "test",
            ),
        )
    result = ah.get_latest_dates("HKQuantityTypeIdentifierStepCount")
    assert "HKQuantityTypeIdentifierStepCount" in result
    assert result["HKQuantityTypeIdentifierStepCount"] is not None
    assert len(result["HKQuantityTypeIdentifierStepCount"]) == 10


def test_import_all_reports_number_of_new_items(isolated_health_db, tmp_path, monkeypatch):
    ah = isolated_health_db
    export_xml = tmp_path / "导出.xml"
    export_xml.write_text(
        """<HealthData>
        <Record type="HKQuantityTypeIdentifierStepCount"
                startDate="2026-08-09 08:00:00 +0800"
                endDate="2026-08-09 08:01:00 +0800" value="100" unit="count" />
        <Record type="HKCategoryTypeIdentifierSleepAnalysis"
                startDate="2026-08-09 00:00:00 +0800"
                endDate="2026-08-09 01:00:00 +0800"
                value="HKCategoryValueSleepAnalysisAsleepDeep" />
        <Workout workoutActivityType="HKWorkoutActivityTypeWalking"
                 startDate="2026-08-09 09:00:00 +0800"
                 endDate="2026-08-09 09:30:00 +0800" duration="30" />
        </HealthData>""",
        encoding="utf-8",
    )
    monkeypatch.setattr(ah, "EXPORT_XML", export_xml)
    progress = []

    total = ah.import_all(progress_callback=progress.append, batch_size=1)

    assert total == 3
    assert progress[-1] == 3


def test_import_all_keeps_older_record_for_new_metric_type(
    isolated_health_db, tmp_path, monkeypatch
):
    ah = isolated_health_db
    with ah._conn() as conn:
        conn.execute(
            """INSERT INTO health_records
               (type, start_date, end_date, value, unit, source_name)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "HKQuantityTypeIdentifierStepCount",
                "2026-09-01 12:00:00 +0800",
                "2026-09-01 12:01:00 +0800",
                100,
                "count",
                "test",
            ),
        )
    export_xml = tmp_path / "导出.xml"
    export_xml.write_text(
        """<HealthData>
        <Record type="HKQuantityTypeIdentifierRestingHeartRate"
                startDate="2026-08-01 08:00:00 +0800"
                endDate="2026-08-01 08:01:00 +0800" value="60"
                unit="count/min" sourceName="test" />
        </HealthData>""",
        encoding="utf-8",
    )
    monkeypatch.setattr(ah, "EXPORT_XML", export_xml)

    total = ah.import_all()

    assert total == 1
    with ah._conn() as conn:
        imported = conn.execute(
            "SELECT COUNT(*) FROM health_records WHERE type = ?",
            ("HKQuantityTypeIdentifierRestingHeartRate",),
        ).fetchone()[0]
    assert imported == 1


def test_import_all_uses_workout_activity_watermark(
    isolated_health_db, tmp_path, monkeypatch
):
    ah = isolated_health_db
    with ah._conn() as conn:
        conn.execute(
            """INSERT INTO health_records
               (type, start_date, end_date, value, unit, source_name)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "HKQuantityTypeIdentifierStepCount",
                "2026-09-01 12:00:00 +0800",
                "2026-09-01 12:01:00 +0800",
                100,
                "count",
                "test",
            ),
        )
        conn.execute(
            """INSERT INTO health_workouts
               (workout_activity_type, start_date, end_date, duration, source_name)
               VALUES (?, ?, ?, ?, ?)""",
            (
                "HKWorkoutActivityTypeWalking",
                "2026-07-01 09:00:00 +0800",
                "2026-07-01 09:30:00 +0800",
                30,
                "test",
            ),
        )
    export_xml = tmp_path / "导出.xml"
    export_xml.write_text(
        """<HealthData>
        <Workout workoutActivityType="HKWorkoutActivityTypeWalking"
                 startDate="2026-08-01 09:00:00 +0800"
                 endDate="2026-08-01 09:30:00 +0800" duration="30"
                 sourceName="test" />
        </HealthData>""",
        encoding="utf-8",
    )
    monkeypatch.setattr(ah, "EXPORT_XML", export_xml)

    total = ah.import_all()

    assert total == 1
    with ah._conn() as conn:
        imported = conn.execute("SELECT COUNT(*) FROM health_workouts").fetchone()[0]
    assert imported == 2


def test_import_all_uses_sleep_stage_watermark(
    isolated_health_db, tmp_path, monkeypatch
):
    ah = isolated_health_db
    with ah._conn() as conn:
        conn.execute(
            """INSERT INTO health_records
               (type, start_date, end_date, value, unit, source_name)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "HKQuantityTypeIdentifierStepCount",
                "2026-09-01 12:00:00 +0800",
                "2026-09-01 12:01:00 +0800",
                100,
                "count",
                "test",
            ),
        )
        conn.execute(
            """INSERT INTO health_sleep
               (type, start_date, end_date, source_name)
               VALUES (?, ?, ?, ?)""",
            (
                "HKCategoryValueSleepAnalysisAsleepDeep",
                "2026-07-01 00:00:00 +0800",
                "2026-07-01 01:00:00 +0800",
                "test",
            ),
        )
    export_xml = tmp_path / "导出.xml"
    export_xml.write_text(
        """<HealthData>
        <Record type="HKCategoryTypeIdentifierSleepAnalysis"
                startDate="2026-08-01 00:00:00 +0800"
                endDate="2026-08-01 01:00:00 +0800"
                value="HKCategoryValueSleepAnalysisAsleepDeep"
                sourceName="test" />
        </HealthData>""",
        encoding="utf-8",
    )
    monkeypatch.setattr(ah, "EXPORT_XML", export_xml)

    total = ah.import_all()

    assert total == 1
    with ah._conn() as conn:
        imported = conn.execute("SELECT COUNT(*) FROM health_sleep").fetchone()[0]
    assert imported == 2


def test_import_all_rechecks_recent_history_for_existing_metric_type(
    isolated_health_db, tmp_path, monkeypatch
):
    ah = isolated_health_db
    with ah._conn() as conn:
        conn.execute(
            """INSERT INTO health_records
               (type, start_date, end_date, value, unit, source_name)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "HKQuantityTypeIdentifierStepCount",
                "2026-09-01 12:00:00 +0800",
                "2026-09-01 12:01:00 +0800",
                100,
                "count",
                "test",
            ),
        )
    export_xml = tmp_path / "导出.xml"
    export_xml.write_text(
        """<HealthData>
        <Record type="HKQuantityTypeIdentifierStepCount"
                startDate="2026-08-20 08:00:00 +0800"
                endDate="2026-08-20 08:01:00 +0800" value="200"
                unit="count" sourceName="test" />
        </HealthData>""",
        encoding="utf-8",
    )
    monkeypatch.setattr(ah, "EXPORT_XML", export_xml)

    total = ah.import_all()

    assert total == 1
    with ah._conn() as conn:
        imported = conn.execute(
            "SELECT COUNT(*) FROM health_records WHERE type = ?",
            ("HKQuantityTypeIdentifierStepCount",),
        ).fetchone()[0]
    assert imported == 2


def test_import_all_reports_only_rows_inserted_during_overlap(
    isolated_health_db, tmp_path, monkeypatch
):
    ah = isolated_health_db
    export_xml = tmp_path / "导出.xml"
    export_xml.write_text(
        """<HealthData>
        <Record type="HKQuantityTypeIdentifierStepCount"
                startDate="2026-09-01 12:00:00 +0800"
                endDate="2026-09-01 12:01:00 +0800" value="100"
                unit="count" sourceName="test" />
        </HealthData>""",
        encoding="utf-8",
    )
    monkeypatch.setattr(ah, "EXPORT_XML", export_xml)

    first_total = ah.import_all()
    second_total = ah.import_all()

    assert first_total == 1
    assert second_total == 0
    with ah._conn() as conn:
        imported = conn.execute("SELECT COUNT(*) FROM health_records").fetchone()[0]
    assert imported == 1


def test_import_all_full_reconcile_imports_history_before_overlap(
    isolated_health_db, tmp_path, monkeypatch
):
    ah = isolated_health_db
    with ah._conn() as conn:
        conn.execute(
            """INSERT INTO health_records
               (type, start_date, end_date, value, unit, source_name)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "HKQuantityTypeIdentifierStepCount",
                "2026-09-01 12:00:00 +0800",
                "2026-09-01 12:01:00 +0800",
                100,
                "count",
                "test",
            ),
        )
    export_xml = tmp_path / "导出.xml"
    export_xml.write_text(
        """<HealthData>
        <Record type="HKQuantityTypeIdentifierStepCount"
                startDate="2026-01-01 08:00:00 +0800"
                endDate="2026-01-01 08:01:00 +0800" value="200"
                unit="count" sourceName="test" />
        </HealthData>""",
        encoding="utf-8",
    )
    monkeypatch.setattr(ah, "EXPORT_XML", export_xml)

    incremental_total = ah.import_all()
    reconcile_total = ah.import_all(full_reconcile=True)

    assert incremental_total == 0
    assert reconcile_total == 1
    with ah._conn() as conn:
        imported = conn.execute("SELECT COUNT(*) FROM health_records").fetchone()[0]
    assert imported == 2


def test_import_all_stops_cooperatively_and_flushes_current_batch(
    isolated_health_db, tmp_path, monkeypatch
):
    ah = isolated_health_db
    export_xml = tmp_path / "导出.xml"
    records = "".join(
        f'<Record type="HKQuantityTypeIdentifierStepCount" '
        f'startDate="2026-08-09 {index:04d}" endDate="2026-08-09 {index:04d}" '
        f'value="1" unit="count" />'
        for index in range(1100)
    )
    export_xml.write_text(f"<HealthData>{records}</HealthData>", encoding="utf-8")
    monkeypatch.setattr(ah, "EXPORT_XML", export_xml)
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(ah.ImportCancelled):
        ah.import_all(batch_size=5000, cancel_event=cancel_event)

    with ah._conn() as conn:
        imported = conn.execute("SELECT COUNT(*) FROM health_records").fetchone()[0]
    assert imported == 999
