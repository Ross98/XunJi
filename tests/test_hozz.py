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


def test_schema_backup_closes_its_destination_connection(isolated_health_db, monkeypatch):
    import sqlite3
    import hozz_sync as hs
    original = sqlite3.connect
    connections = []

    def connect(*args, **kwargs):
        conn = original(*args, **kwargs)
        connections.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, 'connect', connect)
    hs._initialize()
    for conn in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            conn.execute('SELECT 1')


@pytest.mark.parametrize('kind', ['quantity', 'category', 'workout'])
def test_deleting_projection_owner_keeps_unique_surviving_sample(isolated_health_db, tmp_path, kind):
    import hozz_sync as hs
    changes = {}
    if kind == 'category':
        changes = {'type': 'HKCategoryTypeIdentifierSleepAnalysis', 'value': 4}
    elif kind == 'workout':
        changes = {'type': 'HKWorkoutTypeIdentifier', 'activityType': 37, 'duration': 1800}
    first = sample('owner', kind, **changes)
    second = sample('survivor', kind, **changes, source={'name': 'Phone'})
    folder = tmp_path / 'batches'
    batch(folder, 'a.ndjson', [first, second])
    hs.sync(folder)
    batch(folder, 'b.ndjson', [dict(schemaVersion=1, kind='deletion', id='owner', type=first['type'])])
    hs.sync(folder)
    ah = isolated_health_db
    query = ah.query_records if kind == 'quantity' else ah.query_sleep if kind == 'category' else ah.query_workouts
    rows = query()
    assert len(rows) == 1
    assert rows[0]['source_name'] == 'Phone'
    assert hs.status()['samples'] == 1
    assert hs.status()['unprojected'] == 0
    batch(folder, 'c.ndjson', [first])
    hs.sync(folder)
    assert query() == rows
    with ah._conn() as conn:
        owner = conn.execute("SELECT owns_projection FROM hozz_samples WHERE id='survivor'").fetchone()[0]
    assert owner == 1


def test_identical_survivor_remains_visible_after_owner_deletion(isolated_health_db, tmp_path):
    import hozz_sync as hs
    folder = tmp_path / 'batches'
    batch(folder, 'a.ndjson', [sample('owner'), sample('duplicate')])
    hs.sync(folder)
    batch(folder, 'b.ndjson', [dict(schemaVersion=1, kind='deletion', id='owner', type=sample()['type'])])
    hs.sync(folder)
    assert len(isolated_health_db.query_records()) == 1
    assert hs.status()['samples'] == 1
    assert hs.status()['unprojected'] == 0


def test_ambiguous_survivors_stay_unprojected_until_unambiguous(isolated_health_db, tmp_path):
    import hozz_sync as hs
    folder = tmp_path / 'batches'
    batch(folder, 'a.ndjson', [sample('owner'), sample('b', quantity={'value': 200, 'unit': 'count'}),
                              sample('c', quantity={'value': 300, 'unit': 'count'})])
    hs.sync(folder)
    batch(folder, 'b.ndjson', [dict(schemaVersion=1, kind='deletion', id='owner', type=sample()['type'])])
    hs.sync(folder)
    assert isolated_health_db.query_records() == []
    assert hs.status()['unprojected'] == 2
    batch(folder, 'c.ndjson', [sample('d', quantity={'value': 400, 'unit': 'count'})])
    hs.sync(folder)
    assert isolated_health_db.query_records() == []
    batch(folder, 'd.ndjson', [dict(schemaVersion=1, kind='deletion', id=identifier, type=sample()['type'])
                              for identifier in ['c', 'd']])
    hs.sync(folder)
    assert isolated_health_db.query_records()[0]['value'] == 200
    assert hs.status()['unprojected'] == 0


def test_owner_replacement_rolls_back_with_invalid_batch(isolated_health_db, tmp_path):
    import hozz_sync as hs
    folder = tmp_path / 'batches'
    batch(folder, 'a.ndjson', [sample('owner'), sample('survivor', quantity={'value': 200, 'unit': 'count'})])
    hs.sync(folder)
    path = batch(folder, 'b.ndjson', [dict(schemaVersion=1, kind='deletion', id='owner', type=sample()['type'])])
    path.write_text(path.read_text()+'{broken\n')
    assert hs.sync(folder)['status'] == 'partial'
    assert isolated_health_db.query_records()[0]['value'] == 100
    assert hs.status()['samples'] == 2
    with isolated_health_db._conn() as conn:
        assert conn.execute("SELECT owns_projection FROM hozz_samples WHERE id='owner'").fetchone()[0] == 1


def test_legacy_projection_is_not_adopted_or_deleted_during_survivor_reconciliation(isolated_health_db, tmp_path):
    import hozz_sync as hs
    ah = isolated_health_db
    ah._bulk_insert([dict(type=sample()['type'], start_date='2026-09-30 00:00:00 +0800',
                         end_date='2026-09-30 00:01:00 +0800', value=100, unit='count', source_name='Watch')], [])
    before = ah.query_records()
    folder = tmp_path / 'batches'
    batch(folder, 'a.ndjson', [sample('a'), sample('b')])
    hs.sync(folder)
    batch(folder, 'b.ndjson', [dict(schemaVersion=1, kind='deletion', id='a', type=sample()['type'])])
    hs.sync(folder)
    assert ah.query_records() == before
    with ah._conn() as conn:
        assert conn.execute("SELECT owns_projection FROM hozz_samples WHERE id='b'").fetchone()[0] == 0


def test_identical_survivor_ownership_can_transfer_until_last_deletion(isolated_health_db, tmp_path):
    import hozz_sync as hs
    folder = tmp_path / 'batches'
    batch(folder, '0.ndjson', [sample('a'), sample('b'), sample('c')])
    hs.sync(folder)
    for index, identifier in enumerate(['a', 'b', 'c'], 1):
        batch(folder, f'{index}.ndjson', [dict(schemaVersion=1, kind='deletion', id=identifier, type=sample()['type'])])
        hs.sync(folder)
        assert len(isolated_health_db.query_records()) == (1 if identifier != 'c' else 0)
        assert hs.status()['unprojected'] == 0
    batch(folder, 'late.ndjson', [sample('a'), sample('b'), sample('c')])
    hs.sync(folder)
    assert isolated_health_db.query_records() == []
    assert hs.status()['samples'] == 0


@pytest.mark.parametrize('failure', ['huge_number', 'sqlite_integer', 'timezone_overflow', 'nested_json'])
def test_overflowing_or_deep_batch_rolls_back_and_does_not_starve_later_files(
    isolated_health_db, tmp_cache, tmp_path, failure
):
    import hozz_sync as hs
    folder = tmp_path / 'batches'
    first = sample('rolled-back')
    malformed = sample('bad', startDate='2026-09-30T01:00:00Z', endDate='2026-09-30T01:01:00Z')
    if failure == 'huge_number':
        malformed['quantity']['value'] = 10 ** 400
    elif failure == 'sqlite_integer':
        malformed['quantity']['value'] = 2 ** 63
    elif failure == 'timezone_overflow':
        malformed['startDate'] = '9999-12-31T23:00:00Z'
    bad_path = batch(folder, 'a.ndjson', [first, malformed])
    if failure == 'nested_json':
        bad_path.write_text(json.dumps(first) + '\n' + '[' * 1500 + '0' + ']' * 1500 + '\n')
    batch(folder, 'b.ndjson', [sample('survivor')])

    result = hs.sync(folder, cache=tmp_cache)
    assert result['status'] == 'partial'
    assert (result['files'], result['new_samples'], result['inserted']) == (1, 1, 1)
    assert len(result['errors']) == 1
    assert result['errors'][0].startswith('a.ndjson:')
    with isolated_health_db._conn() as conn:
        assert [row[0] for row in conn.execute('SELECT id FROM hozz_samples')] == ['survivor']
        assert conn.execute('SELECT count(*) FROM hozz_files WHERE path=?', (str(bad_path),)).fetchone()[0] == 0
    marker = tmp_cache.get('_health_import_time')
    assert marker
    repeated = hs.sync(folder, cache=tmp_cache)
    assert repeated['status'] == 'partial'
    assert (repeated['files'], repeated['new_samples']) == (0, 0)
    assert tmp_cache.get('_health_import_time') == marker

    # A repaired batch remains retryable; its prior partial writes never escaped.
    batch(folder, 'a.ndjson', [first, sample('repaired', startDate='2026-09-30T02:00:00Z')])
    repaired = hs.sync(folder, cache=tmp_cache)
    assert repaired['status'] == 'ok'
    assert repaired['new_samples'] == 2
    assert hs.status()['samples'] == 3
