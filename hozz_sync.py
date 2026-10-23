"""Import private Hozz NDJSON batches into the existing health dashboard.

Raw records retain UUIDs and full precision. Dashboard projections retain the
legacy keys; collisions are reported rather than overwriting another source.
"""
import json
import math
import os
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import apple_health as ah
from health_values import health_interval, health_timestamp

SYNC_LOCK = threading.Lock()
CHECK_STALE_SECONDS = 180
SLEEP_STAGES = {0: "InBed", 1: "AsleepUnspecified", 2: "Awake",
                3: "AsleepCore", 4: "AsleepDeep", 5: "AsleepREM"}
WORKOUT_TYPES = {13: "Cycling", 20: "FunctionalStrengthTraining", 24: "Hiking",
                 37: "Running", 50: "TraditionalStrengthTraining", 52: "Walking",
                 59: "CoreTraining", 60: "Flexibility", 63: "MixedCardio",
                 68: "Stairs"}


def source_folder():
    return Path(os.environ.get("XUNJI_HEALTH_SYNC_DIR",
        str(Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Apple Health")))


def _initialize():
    # One SQLite backup before adding the importer schema; never recreate caches.
    if ah.HEALTH_DB_PATH.exists():
        with ah._conn() as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='hozz_samples'").fetchone()
            if not exists:
                backup = ah.HEALTH_DB_PATH.with_name(
                    ah.HEALTH_DB_PATH.name + ".pre-hozz-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
                with closing(sqlite3.connect(backup)) as target:
                    conn.backup(target)
    ah._init_db()
    with ah._conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS hozz_samples (
                id TEXT PRIMARY KEY, type TEXT NOT NULL, kind TEXT NOT NULL,
                raw TEXT NOT NULL, table_name TEXT, projected_type TEXT,
                start_date TEXT, owns_projection INTEGER NOT NULL DEFAULT 0,
                projection_state TEXT NOT NULL DEFAULT 'unsupported'
            );
            CREATE TABLE IF NOT EXISTS hozz_deleted (id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS hozz_files (
                path TEXT PRIMARY KEY, signature TEXT NOT NULL, imported_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS hozz_coverage (
                type TEXT PRIMARY KEY, observed_at TEXT NOT NULL, raw TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS hozz_sync_state (id INTEGER PRIMARY KEY, raw TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_hozz_samples_projection
                ON hozz_samples(table_name, projected_type, start_date);
        """)


def _date(value):
    if not isinstance(value, str):
        raise ValueError("Health timestamp must be a string")
    dt = health_timestamp(value)
    return dt.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S %z")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid health numeric value")
    return value


def _stored_object(raw):
    """Damaged metadata must not break a status read or the next sync."""
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            return None
        json.dumps(value, allow_nan=False)
        return value
    except (TypeError, ValueError, RecursionError):
        return None


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return None
        observed.astimezone(ZoneInfo("Asia/Shanghai"))  # Must also be displayable.
        return observed
    except (ValueError, OverflowError):
        return None


def _coverage_status(row):
    complete, state = row.get("complete"), row.get("state")
    if state is not None and not isinstance(state, str):
        return "uncertain"
    if complete is True and state not in ("draining", "authorizationIndeterminate"):
        return "complete"
    if complete is False and state == "draining":
        return "incomplete"
    return "uncertain"


def _projection(r):
    kind = r["kind"]
    if kind not in ("quantity", "category", "workout"):
        return None
    source = r.get("source", {})
    if not isinstance(source, dict):
        raise ValueError("Health source must be an object")
    health_interval(r["startDate"], r["endDate"])
    row = dict(start_date=_date(r["startDate"]), end_date=_date(r["endDate"]),
               source_name=source.get("name", ""))
    if kind == "quantity":
        if not isinstance(r["quantity"], dict):
            raise ValueError("Health quantity must be an object")
        q = r["quantity"].get("canonical", r["quantity"])
        if not isinstance(q, dict):
            raise ValueError("Canonical quantity must be an object")
        unit, value = q["unit"], _number(q["value"])
        # Convert only documented equivalent units; never guess unknown units.
        if unit == "s" and r["type"] in (
                "HKQuantityTypeIdentifierAppleStandTime", "HKQuantityTypeIdentifierAppleExerciseTime"):
            value, unit = value / 60, "min"
        row.update(type=r["type"], value=value, unit=unit)
        return "health_records", "type", row
    if kind == "category":
        value = _number(r["value"])
        if r["type"] == "HKCategoryTypeIdentifierSleepAnalysis":
            if value not in SLEEP_STAGES:
                raise ValueError("Unknown sleep stage")
            row["type"] = "HKCategoryValueSleepAnalysis" + SLEEP_STAGES[value]
            return "health_sleep", "type", row
        row.update(type=r["type"], value=value, unit="count")
        return "health_records", "type", row
    activity = r["activityType"]
    activity = ("HKWorkoutActivityType" + WORKOUT_TYPES.get(activity, str(activity))
                if isinstance(activity, int) else activity)
    duration = _number(r["duration"])
    if duration < 0:
        raise ValueError("Workout duration must not be negative")
    row.update(workout_activity_type=activity, duration=duration / 60,
               duration_unit="min", total_distance=None, total_distance_unit=None,
               total_energy_burned=None, total_energy_burned_unit=None)
    # Preserve any remaining workout metadata in raw; optional summaries are not invented.
    return "health_workouts", "workout_activity_type", row


def _restore_surviving_projection(conn, table, projected_type, start, counts):
    keys = {'health_records': 'type', 'health_sleep': 'type',
            'health_workouts': 'workout_activity_type'}
    if table not in keys:
        return
    key = keys[table]
    if conn.execute(f"SELECT 1 FROM {table} WHERE {key}=? AND start_date=?",
                    (projected_type, start)).fetchone():
        return  # Existing projections, including legacy XML, retain ownership.
    candidates = conn.execute("""SELECT id, raw FROM hozz_samples h
        WHERE table_name=? AND projected_type=? AND start_date=?
        AND NOT EXISTS (SELECT 1 FROM hozz_deleted d WHERE d.id=h.id)
        ORDER BY id""", (table, projected_type, start))
    first = candidates.fetchone()
    if first is None:
        return
    conn.execute("""UPDATE hozz_samples SET owns_projection=0, projection_state='collision'
        WHERE table_name=? AND projected_type=? AND start_date=?
        AND (owns_projection!=0 OR projection_state!='collision')""", (table, projected_type, start))

    def candidate_row(candidate):
        try:
            projection = _projection(json.loads(candidate['raw']))
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return None  # Legacy invalid samples remain unresolved, never block a deletion.
        if (not projection or projection[0] != table or projection[1] != key
                or projection[2][key] != projected_type or projection[2]['start_date'] != start):
            raise ValueError('Stored Hozz projection identity changed')
        return projection[2]

    row = candidate_row(first)
    if row is None:
        return
    for candidate in candidates:
        if candidate_row(candidate) != row:
            return  # Conflicting survivors have no defensible priority.
    columns = list(row)
    conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                 list(row.values()))
    conn.execute("""UPDATE hozz_samples SET projection_state='legacy_duplicate'
        WHERE table_name=? AND projected_type=? AND start_date=?""", (table, projected_type, start))
    conn.execute("UPDATE hozz_samples SET owns_projection=1, projection_state='projected' WHERE id=?",
                 (first['id'],))
    counts['inserted'] += 1


def _ingest(conn, r, counts):
    if not isinstance(r, dict) or type(r.get("schemaVersion")) is not int or r["schemaVersion"] != 1:
        raise ValueError("Unsupported Hozz schemaVersion")
    kind = r.get("kind")
    if kind == "hozzConnectionTest":
        return
    if kind == "typeCoverage":
        if (not isinstance(r.get("type"), str) or not r["type"].strip()
                or ("complete" in r and type(r["complete"]) is not bool)
                or ("state" in r and not isinstance(r["state"], str))):
            raise ValueError("Invalid Hozz coverage fields")
        _date(r["observedAt"])  # Reject naive timestamps before persisting coverage.
        observed = datetime.fromisoformat(r["observedAt"].replace("Z", "+00:00"))
        old = conn.execute("SELECT observed_at FROM hozz_coverage WHERE type=?", (r["type"],)).fetchone()
        previous = _timestamp(old[0]) if old else None
        if previous is None or observed >= previous:
            conn.execute("INSERT OR REPLACE INTO hozz_coverage VALUES (?,?,?)",
                         (r["type"], observed.isoformat(), json.dumps(r, allow_nan=False)))
        return
    identifier = r.get("id")
    if not isinstance(identifier, str) or not identifier or not isinstance(r.get("type"), str):
        raise ValueError("Hozz record missing id/type")
    if kind == "deletion":
        conn.execute("INSERT OR IGNORE INTO hozz_deleted VALUES (?)", (identifier,))
        old = conn.execute("SELECT * FROM hozz_samples WHERE id=?", (identifier,)).fetchone()
        if old:
            if old["owns_projection"]:
                table = old["table_name"]
                key = "workout_activity_type" if table == "health_workouts" else "type"
                conn.execute(f"DELETE FROM {table} WHERE {key}=? AND start_date=?",
                             (old["projected_type"], old["start_date"]))
            conn.execute("DELETE FROM hozz_samples WHERE id=?", (identifier,))
            _restore_surviving_projection(conn, old['table_name'], old['projected_type'], old['start_date'], counts)
            counts["deleted"] += 1
        return
    if conn.execute("SELECT 1 FROM hozz_deleted WHERE id=?", (identifier,)).fetchone():
        return
    # Redelivered records do not duplicate projections, including delayed batches.
    if conn.execute("SELECT 1 FROM hozz_samples WHERE id=?", (identifier,)).fetchone():
        return
    projection = _projection(r)
    table, projected_type, start, owns, state = None, None, None, 0, "unsupported"
    if projection:
        table, key, row = projection
        projected_type, start = row[key], row["start_date"]
        old = conn.execute(f"SELECT * FROM {table} WHERE {key}=? AND start_date=?",
                           (projected_type, start)).fetchone()
        if old:
            same = all(old[k] == v or (isinstance(v, (int, float)) and
                       isinstance(old[k], (int, float)) and math.isclose(old[k], v, rel_tol=1e-8))
                       for k, v in row.items())
            state = "legacy_duplicate" if same else "collision"
        elif conn.execute("""SELECT 1 FROM hozz_samples WHERE table_name=?
                AND projected_type=? AND start_date=? LIMIT 1""", (table, projected_type, start)).fetchone():
            state = 'collision'
        else:
            columns = list(row)
            conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                         list(row.values()))
            owns, state = 1, "projected"
            counts["inserted"] += 1
    conn.execute("INSERT INTO hozz_samples VALUES (?,?,?,?,?,?,?,?,?)",
                 (identifier, r["type"], kind, json.dumps(r, ensure_ascii=False),
                  table, projected_type, start, owns, state))
    if projection and state == 'collision':
        _restore_surviving_projection(conn, table, projected_type, start, counts)
    counts["new_samples"] += 1


def sync(folder=None, cache=None, cancel_event=None):
    """Atomically ingest each complete batch; failed files remain retryable."""
    folder = Path(folder) if folder is not None else source_folder()
    with SYNC_LOCK:
        _initialize()
        result = dict(status="ok", folder=str(folder), files=0, new_samples=0,
                      inserted=0, deleted=0, errors=[], checked_at=datetime.now(timezone.utc).isoformat())
        try:
            if not folder.is_dir():
                raise FileNotFoundError("同步文件夹不存在或不可读取")
            files = sorted(folder.glob("*.ndjson"))
        except OSError as exc:
            result.update(status="unavailable", errors=[str(exc)])
            files = []
        for path in files:
            if cancel_event is not None and cancel_event.is_set():
                result["status"] = "cancelled"
                break
            counts = dict(new_samples=0, inserted=0, deleted=0)
            try:
                before = path.stat()
                signature = f"{before.st_size}:{before.st_mtime_ns}"
                with ah._conn() as conn:
                    old = conn.execute("SELECT signature FROM hozz_files WHERE path=?", (str(path),)).fetchone()
                    if old and old[0] == signature:
                        continue
                    # Complete lossless batches end with a newline. Incomplete downloads retry.
                    with path.open("r", encoding="utf-8") as stream:
                        for line in stream:
                            if cancel_event is not None and cancel_event.is_set():
                                raise ah.ImportCancelled("Hozz sync cancelled")
                            if not line.endswith("\n"):
                                raise ValueError("Incomplete NDJSON batch")
                            if line.strip():
                                _ingest(conn, json.loads(line), counts)
                    after = path.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError("Batch changed during import; retry later")
                    conn.execute("INSERT OR REPLACE INTO hozz_files VALUES (?,?,?)",
                                 (str(path), signature, result["checked_at"]))
                result["files"] += 1
                for key, value in counts.items():
                    result[key] += value
            except ah.ImportCancelled:
                result["status"] = "cancelled"
                break
            except (OSError, ValueError, KeyError, TypeError, OverflowError,
                    RecursionError, sqlite3.Error) as exc:
                result["errors"].append(f"{path.name}: {exc}")
        if result["errors"] and result["status"] == "ok":
            result["status"] = "partial"
        with ah._conn() as conn:
            previous = conn.execute("SELECT raw FROM hozz_sync_state WHERE id=1").fetchone()
            previous = _stored_object(previous[0]) if previous else None
            result["last_sync"] = (result["checked_at"] if result["files"] else
                                   previous.get("last_sync") if previous else None)
            conn.execute("INSERT OR REPLACE INTO hozz_sync_state VALUES (1,?)", (json.dumps(result),))
        if cache is not None and (result["new_samples"] or result["deleted"]):
            cache.set("_health_import_time", {"time": result["checked_at"]})
        return result


def status():
    """Read persisted status without mutating the database during page rendering."""
    empty = dict(folder=str(source_folder()), status="idle", last_sync=None, errors=[],
                 inserted=0, samples=0, complete=0, incomplete=0, uncertain=0,
                 unprojected=0, coverage=[], last_folder=None, checked_at=None,
                 checked_at_bj=None, last_sync_bj=None, check_stale=True)
    with ah._conn() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='hozz_samples'").fetchone():
            return empty
        last = conn.execute("SELECT raw FROM hozz_sync_state WHERE id=1").fetchone()
        saved = _stored_object(last[0]) if last else {}
        if saved is None:
            empty["errors"].append("上次同步状态损坏，等待重新检查。")
            saved = {}
        # Persisted metadata cannot override derived freshness or coverage flags.
        for field in ("folder", "checked_at", "last_sync"):
            if isinstance(saved.get(field), str):
                empty[field] = saved[field]
        saved_status = saved.get("status")
        if isinstance(saved_status, str) and saved_status in ("ok", "partial", "unavailable", "cancelled"):
            empty["status"] = saved_status
        for field in ("files", "new_samples", "inserted", "deleted"):
            value = saved.get(field)
            empty[field] = value if type(value) is int and value >= 0 else 0
        errors = saved.get("errors", [])
        if isinstance(errors, list):
            empty["errors"].extend(error for error in errors if isinstance(error, str))
        # Persisted paths may belong to another Mac or login; show this process's
        # configured destination separately from the last check's provenance.
        empty["last_folder"] = saved.get("folder") if isinstance(saved.get("folder"), str) else None
        empty["folder"] = str(source_folder())
        checked = _timestamp(empty["checked_at"])
        if checked is not None:
            empty["checked_at_bj"] = _date(empty["checked_at"])
            age = (datetime.now(timezone.utc) - checked).total_seconds()
            empty["check_stale"] = not 0 <= age <= CHECK_STALE_SECONDS
        empty["samples"] = conn.execute("SELECT COUNT(*) FROM hozz_samples").fetchone()[0]
        empty["unprojected"] = conn.execute(
            "SELECT COUNT(*) FROM hozz_samples WHERE projection_state IN ('unsupported','collision')").fetchone()[0]
        coverage = []
        for row in conn.execute("SELECT type, raw FROM hozz_coverage ORDER BY type"):
            item = _stored_object(row["raw"]) or {}
            classification = _coverage_status(item)
            # The stored key remains visible even when its JSON is damaged.
            identifier = row["type"]
            if not isinstance(identifier, str) or not identifier.strip():
                identifier, classification = "未知类型", "uncertain"
            elif item.get("type") != identifier:
                classification = "uncertain"
            item["type"] = identifier
            item["state"] = item.get("state") if isinstance(item.get("state"), str) else "unconfirmed"
            item["coverage_status"] = classification
            coverage.append(item)
        empty["coverage"] = coverage
        for classification in ("complete", "incomplete", "uncertain"):
            empty[classification] = sum(r["coverage_status"] == classification for r in coverage)
        if _timestamp(empty["last_sync"]) is not None:
            empty["last_sync_bj"] = _date(empty["last_sync"])
    return empty
