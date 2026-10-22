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
        """)


def _date(value):
    if not isinstance(value, str):
        raise ValueError("Health timestamp must be a string")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Health timestamp requires a timezone")
    return dt.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S %z")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid health numeric value")
    return value


def _projection(r):
    kind = r["kind"]
    if kind not in ("quantity", "category", "workout"):
        return None
    source = r.get("source", {})
    if not isinstance(source, dict):
        raise ValueError("Health source must be an object")
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
    row.update(workout_activity_type=activity, duration=_number(r["duration"]) / 60,
               duration_unit="min", total_distance=None, total_distance_unit=None,
               total_energy_burned=None, total_energy_burned_unit=None)
    # Preserve any remaining workout metadata in raw; optional summaries are not invented.
    return "health_workouts", "workout_activity_type", row


def _ingest(conn, r, counts):
    if not isinstance(r, dict) or r.get("schemaVersion") != 1:
        raise ValueError("Unsupported Hozz schemaVersion")
    kind = r.get("kind")
    if kind == "hozzConnectionTest":
        return
    if kind == "typeCoverage":
        _date(r["observedAt"])  # Reject naive timestamps before persisting coverage.
        observed = datetime.fromisoformat(r["observedAt"].replace("Z", "+00:00"))
        old = conn.execute("SELECT observed_at FROM hozz_coverage WHERE type=?", (r["type"],)).fetchone()
        if old is None or observed >= datetime.fromisoformat(old[0]):
            conn.execute("INSERT OR REPLACE INTO hozz_coverage VALUES (?,?,?)",
                         (r["type"], observed.isoformat(), json.dumps(r)))
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
        else:
            columns = list(row)
            conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                         list(row.values()))
            owns, state = 1, "projected"
            counts["inserted"] += 1
    conn.execute("INSERT INTO hozz_samples VALUES (?,?,?,?,?,?,?,?,?)",
                 (identifier, r["type"], kind, json.dumps(r, ensure_ascii=False),
                  table, projected_type, start, owns, state))
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
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                result["errors"].append(f"{path.name}: {exc}")
        if result["errors"] and result["status"] == "ok":
            result["status"] = "partial"
        with ah._conn() as conn:
            previous = conn.execute("SELECT raw FROM hozz_sync_state WHERE id=1").fetchone()
            result["last_sync"] = (result["checked_at"] if result["files"] else
                                   json.loads(previous[0]).get("last_sync") if previous else None)
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
        if last:
            empty.update(json.loads(last[0]))
        # Persisted paths may belong to another Mac or login; show this process's
        # configured destination separately from the last check's provenance.
        empty["last_folder"] = empty["folder"] if last else None
        empty["folder"] = str(source_folder())
        if empty.get("checked_at"):
            try:
                checked = datetime.fromisoformat(empty["checked_at"])
                empty["checked_at_bj"] = _date(empty["checked_at"])
                empty["check_stale"] = (
                    datetime.now(timezone.utc) - checked
                ).total_seconds() > CHECK_STALE_SECONDS
            except (TypeError, ValueError):
                empty["check_stale"] = True
        empty["samples"] = conn.execute("SELECT COUNT(*) FROM hozz_samples").fetchone()[0]
        empty["unprojected"] = conn.execute(
            "SELECT COUNT(*) FROM hozz_samples WHERE projection_state IN ('unsupported','collision')").fetchone()[0]
        coverage = [json.loads(r[0]) for r in conn.execute("SELECT raw FROM hozz_coverage ORDER BY type")]
        empty["coverage"] = coverage
        empty["complete"] = sum(r.get("complete") is True for r in coverage)
        empty["incomplete"] = sum(r.get("state") == "draining" for r in coverage)
        empty["uncertain"] = sum(r.get("complete") is not True and r.get("state") != "draining" for r in coverage)
        empty["last_sync_bj"] = (_date(empty["last_sync"]) if empty.get("last_sync") else None)
    return empty
