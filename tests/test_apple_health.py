import sys; sys.path.insert(0, '.')

import threading

import pytest


@pytest.mark.parametrize("query_name", ["query_records", "query_records_desc", "query_workouts", "query_sleep", "get_sleep_summary"])
def test_timestamp_date_bounds_include_the_first_day(isolated_health_db, query_name):
    ah = isolated_health_db
    common = dict(start_date="2026-07-24 08:00:00 +0800", end_date="2026-07-24 09:00:00 +0800", source_name="test")
    ah._bulk_insert([dict(common, type="steps", value=100, unit="count")],
        [dict(common, workout_activity_type="Walking", duration=60, duration_unit="min",
              total_distance=None, total_distance_unit=None, total_energy_burned=None, total_energy_burned_unit=None)],
        [dict(common, type="HKCategoryValueSleepAnalysisAsleepCore")])
    query = getattr(ah, query_name)
    plain = query(start="2026-07-24", end="2026-07-25")
    assert plain
    assert query(start="2026-07-24 00:00:00", end="2026-07-25 23:59:59") == plain

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


def test_coverage_combines_all_tables_and_empty_stores(isolated_health_db):
    ah = isolated_health_db
    assert ah.get_data_coverage() == {'start_date': None, 'end_date': None}
    with ah._conn() as conn:
        conn.execute('INSERT INTO health_records (type,start_date,end_date) VALUES (?,?,?)',
                     ('steps', '2026-10-10 01:00:00 +0800', '2026-10-10 02:00:00 +0800'))
        conn.execute('INSERT INTO health_workouts (workout_activity_type,start_date,end_date) VALUES (?,?,?)',
                     ('Walking', '2026-10-09 01:00:00 +0800', '2026-10-09 02:00:00 +0800'))
        conn.execute('INSERT INTO health_sleep (type,start_date,end_date) VALUES (?,?,?)',
                     ('AsleepCore', '2026-10-21 23:00:00 +0800', '2026-10-22 06:00:00 +0800'))
    assert ah.get_data_coverage() == {'start_date': '2026-10-09', 'end_date': '2026-10-21'}


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-Infinity", "1e9999"])
@pytest.mark.parametrize("field", ["value", "duration", "totalDistance", "totalEnergyBurned"])
def test_xml_import_rejects_nonfinite_numbers_without_losing_valid_neighbors(
    isolated_health_db, tmp_path, monkeypatch, field, invalid
):
    import json
    import xml.etree.ElementTree as ET

    ah = isolated_health_db
    root = ET.Element("HealthData")
    for hour, bad in [(8, True), (9, False)]:
        common = dict(startDate=f"2026-08-09 {hour:02d}:00:00 +0800",
                      endDate=f"2026-08-09 {hour:02d}:30:00 +0800")
        if field == "value":
            ET.SubElement(root, "Record", dict(common,
                type="HKQuantityTypeIdentifierStepCount", unit="count",
                value=invalid if bad else "0"))
        else:
            attrs = dict(common, workoutActivityType="HKWorkoutActivityTypeWalking",
                         duration="30", totalDistance="0", totalEnergyBurned="0")
            if bad:
                attrs[field] = invalid
            ET.SubElement(root, "Workout", attrs)
    xml = tmp_path / "finite-numbers.xml"
    ET.ElementTree(root).write(xml, encoding="utf-8")
    monkeypatch.setattr(ah, "EXPORT_XML", xml)

    assert ah.import_all() == 1
    records = ah.query_records() if field == "value" else ah.query_workouts()
    assert len(records) == 1
    assert records[0]["start_date"] == "2026-08-09 09:00:00 +0800"
    assert records[0]["value" if field == "value" else "total_distance"] == 0
    json.dumps(records, allow_nan=False)


@pytest.mark.parametrize('kind', ['record', 'workout'])
@pytest.mark.parametrize('skipped', [False, True])
def test_streaming_xml_releases_completed_root_children(
    isolated_health_db, tmp_path, monkeypatch, kind, skipped
):
    from datetime import datetime, timedelta

    ah = isolated_health_db
    count = 6000
    base = datetime(2026, 6, 1, 8)
    xml = tmp_path / 'streaming.xml'
    nodes = []
    for index in range(count):
        instant = (base + timedelta(seconds=index)).strftime('%Y-%m-%d %H:%M:%S +0800')
        common = f'startDate="{instant}" endDate="{instant}" sourceName="synthetic"'
        if kind == 'record':
            nodes.append(f'<Record type="steps" value="1" unit="count" {common}>\n'
                         '<MetadataEntry key="test" value="nested"/></Record>')
        else:
            nodes.append(f'<Workout workoutActivityType="Walking" duration="1" durationUnit="min" {common}>\n'
                         '<WorkoutStatistics type="test" sum="1"/></Workout>')
    xml.write_text('<HealthData>' + ''.join(nodes) + '</HealthData>')
    monkeypatch.setattr(ah, 'EXPORT_XML', xml)
    if skipped:
        common = dict(start_date='2026-10-01 08:00:00 +0800', end_date='2026-10-01 08:00:00 +0800', source_name='synthetic')
        ah._bulk_insert([dict(common, type='steps', value=1, unit='count')], [dict(common,
            workout_activity_type='Walking', duration=1, duration_unit='min',
            total_distance=None, total_distance_unit=None, total_energy_burned=None,
            total_energy_burned_unit=None)])

    original = ah.ET.iterparse
    retained = []
    final_children = []

    def observe(source, events):
        root = None
        for event, elem in original(source, events=('start', 'end')):
            if root is None:
                root = elem
            if event == 'end' and elem.tag in ('Record', 'Workout'):
                retained.append(len(root))
            if event == 'end' and elem is root:
                final_children.append(len(root))
            if event in events:
                yield event, elem

    monkeypatch.setattr(ah.ET, 'iterparse', observe)
    assert ah.import_all(batch_size=500) == (0 if skipped else count)
    assert len(retained) == count
    assert max(retained) < count // 2, 'completed XML shells accumulated with file size'
    assert final_children == [0]
    stored = ah.query_records('steps') if kind == 'record' else ah.query_workouts('Walking')
    assert len(stored) == (1 if skipped else count)
    assert all(row['source_name'] == 'synthetic' for row in stored)
