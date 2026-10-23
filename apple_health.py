"""
Apple Health 导出数据导入与查询模块。

从 Apple Health 导出 XML 中流式解析并缓存到 SQLite，
提供各类健康指标和训练数据的查询接口。
"""

import sqlite3
import logging
import math
from contextlib import contextmanager
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date, timezone
from pathlib import Path
from typing import Optional
from health_values import health_interval


logger = logging.getLogger(__name__)
_IMPORT_OVERLAP_DAYS = 30


class ImportCancelled(Exception):
    """Raised after safely flushing the current batch during project shutdown."""


HEALTH_DB_PATH = Path(__file__).parent / "apple_health_cache.sqlite"
# Apple Health 导出目录
EXPORT_DIR = Path(__file__).parent / "Apple Health" / "apple_health_export"
EXPORT_XML = EXPORT_DIR / "导出.xml"


@contextmanager
def _conn():
    conn = sqlite3.connect(str(HEALTH_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _init_db():
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS health_records (
                type        TEXT NOT NULL,
                start_date  TEXT NOT NULL,
                end_date    TEXT NOT NULL,
                value       REAL,
                unit        TEXT,
                source_name TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (type, start_date)
            );
            CREATE TABLE IF NOT EXISTS health_workouts (
                workout_activity_type    TEXT NOT NULL,
                start_date               TEXT NOT NULL,
                end_date                 TEXT NOT NULL,
                duration                 REAL,
                duration_unit            TEXT,
                total_distance           REAL,
                total_distance_unit      TEXT,
                total_energy_burned      REAL,
                total_energy_burned_unit TEXT,
                source_name      TEXT,
                created_at       TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (workout_activity_type, start_date)
            );
            CREATE TABLE IF NOT EXISTS health_sleep (
                type        TEXT NOT NULL,
                start_date  TEXT NOT NULL,
                end_date    TEXT NOT NULL,
                source_name TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (type, start_date)
            );
            CREATE INDEX IF NOT EXISTS idx_records_type ON health_records(type);
            CREATE INDEX IF NOT EXISTS idx_records_date ON health_records(start_date);
            CREATE INDEX IF NOT EXISTS idx_workouts_type ON health_workouts(workout_activity_type);
            CREATE INDEX IF NOT EXISTS idx_sleep_type ON health_sleep(type);
            CREATE TABLE IF NOT EXISTS xml_import_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                incomplete INTEGER NOT NULL CHECK (incomplete IN (0, 1))
            );
        """)


# ---------- 导入 ----------


def _check_import_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise ImportCancelled("Apple Health import cancelled")


def _begin_xml_import():
    """Only a known completed XML run makes date watermarks safe to reuse."""
    with _conn() as conn:
        row = conn.execute("SELECT incomplete FROM xml_import_state WHERE id = 1").fetchone()
        conn.execute("INSERT OR REPLACE INTO xml_import_state VALUES (1, 1)")
    return row is not None and row["incomplete"] == 0


def _finish_xml_import():
    with _conn() as conn:
        conn.execute("INSERT OR REPLACE INTO xml_import_state VALUES (1, 0)")


def _load_import_watermarks():
    with _conn() as conn:
        record_rows = conn.execute(
            "SELECT type, MAX(start_date) AS latest FROM health_records GROUP BY type"
        ).fetchall()
        workout_rows = conn.execute(
            """SELECT workout_activity_type AS type, MAX(start_date) AS latest
               FROM health_workouts GROUP BY workout_activity_type"""
        ).fetchall()
        sleep_rows = conn.execute(
            "SELECT type, MAX(start_date) AS latest FROM health_sleep GROUP BY type"
        ).fetchall()
    return (
        {row["type"]: row["latest"] for row in record_rows},
        {row["type"]: row["latest"] for row in workout_rows},
        {row["type"]: row["latest"] for row in sleep_rows},
    )


def _is_before_import_window(start_date: str, watermark: Optional[str]) -> bool:
    if not watermark:
        return False
    try:
        latest = datetime.strptime(watermark[:19], "%Y-%m-%d %H:%M:%S")
        cutoff = latest - timedelta(days=_IMPORT_OVERLAP_DAYS)
    except (TypeError, ValueError, OverflowError):
        return False
    return start_date[:19] < cutoff.strftime("%Y-%m-%d %H:%M:%S")


def import_all(
    progress_callback=None,
    batch_size=5000,
    cache=None,
    cancel_event=None,
    full_reconcile=False,
):
    """流式导入导出.xml；默认增量导入，可完整核对全部历史。"""
    _check_import_cancelled(cancel_event)
    _init_db()
    _check_import_cancelled(cancel_event)
    xml_path = str(EXPORT_XML)
    if not Path(xml_path).exists():
        raise FileNotFoundError(f"未找到 Apple Health 导出文件: {xml_path}")
    _check_import_cancelled(cancel_event)

    if full_reconcile:
        record_watermarks, workout_watermarks, sleep_watermarks = {}, {}, {}
    else:
        record_watermarks, workout_watermarks, sleep_watermarks = _load_import_watermarks()
    _check_import_cancelled(cancel_event)

    total = 0
    processed = 0
    record_batch = []
    workout_batch = []
    sleep_batch = []

    def flush_pending():
        nonlocal total
        if record_batch or workout_batch or sleep_batch:
            total += _bulk_insert(record_batch, workout_batch, sleep_batch)
            record_batch.clear()
            workout_batch.clear()
            sleep_batch.clear()

    def cancel_if_requested():
        if cancel_event is not None and cancel_event.is_set():
            flush_pending()
            _check_import_cancelled(cancel_event)

    with open(xml_path, "rb") as xml_stream:
        events = ET.iterparse(xml_stream, events=("start", "end"))
        _, root = next(events)
        _check_import_cancelled(cancel_event)
        if root.tag != "HealthData":
            raise ValueError("不是 Apple Health 导出文件")
        if not _begin_xml_import():
            record_watermarks, workout_watermarks, sleep_watermarks = {}, {}, {}
        _check_import_cancelled(cancel_event)
        # elem.clear() alone leaves an empty child attached to HealthData.
        # Root attributes are unused; release its references as elements finish.
        for event, elem in events:
            if event != "end":
                continue
            cancel_if_requested()
            if elem.tag == "Record":
                t = elem.get("type", "")
                sd = elem.get("startDate", "")
                if t == "HKCategoryTypeIdentifierSleepAnalysis":
                    watermark = sleep_watermarks.get(elem.get("value", ""))
                else:
                    watermark = record_watermarks.get(t)
                if _is_before_import_window(sd, watermark):
                    elem.clear()
                    root.clear()
                    continue
                if t == "HKCategoryTypeIdentifierSleepAnalysis":
                    rec = _parse_sleep(elem)
                    if rec:
                        sleep_batch.append(rec)
                        processed += 1
                else:
                    rec = _parse_record(elem)
                    if rec:
                        record_batch.append(rec)
                        processed += 1
            elif elem.tag == "Workout":
                sd = elem.get("startDate", "")
                watermark = workout_watermarks.get(elem.get("workoutActivityType", ""))
                if _is_before_import_window(sd, watermark):
                    elem.clear()
                    root.clear()
                    continue
                wo = _parse_workout(elem)
                if wo:
                    workout_batch.append(wo)
                    processed += 1

            elem.clear()
            root.clear()

            if len(record_batch) >= batch_size or len(workout_batch) >= batch_size or len(sleep_batch) >= batch_size:
                flush_pending()
                if progress_callback:
                    progress_callback(processed)

    cancel_if_requested()
    flush_pending()
    if progress_callback:
        progress_callback(processed)
    _check_import_cancelled(cancel_event)

    # 记录导入时间到 xunji_cache.sqlite
    try:
        if cache is None:
            from cache import Cache
            cache = Cache()
    except ImportCancelled:
        raise
    except Exception:
        logger.exception("Failed to open Apple Health import timestamp cache")
    _check_import_cancelled(cancel_event)
    _finish_xml_import()
    try:
        if cache is not None:
            cache.set("_health_import_time", {"time": datetime.now(timezone.utc).isoformat()})
    except Exception:
        logger.exception("Failed to record Apple Health import timestamp")
    return total


def _optional_finite_number(raw, *, nonnegative=False):
    if not raw:
        return None
    value = float(raw)
    if not math.isfinite(value) or (nonnegative and value < 0):
        raise ValueError("Invalid Apple Health numeric value")
    return value


def _parse_record(elem):
    try:
        health_interval(elem.get("startDate"), elem.get("endDate"))
        val = elem.get("value")
        return {
            "type": elem.get("type", ""),
            "start_date": elem.get("startDate", ""),
            "end_date": elem.get("endDate", ""),
            "value": _optional_finite_number(val),
            "unit": elem.get("unit", ""),
            "source_name": elem.get("sourceName", ""),
        }
    except (ValueError, TypeError):
        return None


def _parse_workout(elem):
    dist_raw = elem.get("totalDistance")
    cal_raw = elem.get("totalEnergyBurned")
    try:
        health_interval(elem.get("startDate"), elem.get("endDate"))
        return {
            "workout_activity_type": elem.get("workoutActivityType", ""),
            "start_date": elem.get("startDate", ""),
            "end_date": elem.get("endDate", ""),
            "duration": _optional_finite_number(elem.get("duration"), nonnegative=True),
            "duration_unit": elem.get("durationUnit", ""),
            "total_distance": _optional_finite_number(dist_raw, nonnegative=True),
            "total_distance_unit": elem.get("totalDistanceUnit", ""),
            "total_energy_burned": _optional_finite_number(cal_raw, nonnegative=True),
            "total_energy_burned_unit": elem.get("totalEnergyBurnedUnit", ""),
            "source_name": elem.get("sourceName", ""),
        }
    except (ValueError, TypeError):
        return None


def _parse_sleep(elem):
    """解析睡眠 CategoryRecord。value 是 HKCategoryValueSleepAnalysis* 枚举。"""
    try:
        health_interval(elem.get("startDate"), elem.get("endDate"))
    except (ValueError, TypeError):
        return None
    return {
        "type": elem.get("value", ""),
        "start_date": elem.get("startDate", ""),
        "end_date": elem.get("endDate", ""),
        "source_name": elem.get("sourceName", ""),
    }


def _bulk_insert(records, workouts, sleep_records=None):
    with _conn() as conn:
        before = conn.total_changes
        if records:
            conn.executemany(
                """INSERT OR IGNORE INTO health_records
                   (type, start_date, end_date, value, unit, source_name)
                   VALUES (:type, :start_date, :end_date, :value, :unit, :source_name)""",
                records,
            )
        if workouts:
            conn.executemany(
                """INSERT OR IGNORE INTO health_workouts
                   (workout_activity_type, start_date, end_date,
                    duration, duration_unit,
                    total_distance, total_distance_unit,
                    total_energy_burned, total_energy_burned_unit,
                    source_name)
                   VALUES (:workout_activity_type, :start_date, :end_date,
                           :duration, :duration_unit,
                           :total_distance, :total_distance_unit,
                           :total_energy_burned, :total_energy_burned_unit,
                           :source_name)""",
                workouts,
            )
        if sleep_records:
            conn.executemany(
                """INSERT OR IGNORE INTO health_sleep
                   (type, start_date, end_date, source_name)
                   VALUES (:type, :start_date, :end_date, :source_name)""",
                sleep_records,
            )
        inserted = conn.total_changes - before
    return inserted


# ---------- 查询 ----------

def query_records(type_filter: str = None,
                  start: str = None,
                  end: str = None,
                  limit: int = None) -> list[dict]:
    """查询健康记录，支持按类型和日期范围过滤。"""
    sql = "SELECT * FROM health_records WHERE 1=1"
    params = []
    if type_filter:
        sql += " AND type = ?"
        params.append(type_filter)
    if start:
        sql += " AND substr(start_date,1,10) >= ?"
        params.append(start[:10])
    if end:
        sql += " AND substr(start_date,1,10) <= ?"
        params.append(end[:10])
    sql += " ORDER BY start_date"
    if limit:
        sql += f" LIMIT {limit}"

    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def query_workouts(activity_type: str = None,
                   start: str = None,
                   end: str = None,
                   limit: int = None) -> list[dict]:
    """查询 Apple Watch 训练记录。"""
    sql = "SELECT * FROM health_workouts WHERE 1=1"
    params = []
    if activity_type:
        sql += " AND workout_activity_type = ?"
        params.append(activity_type)
    if start:
        sql += " AND substr(start_date,1,10) >= ?"
        params.append(start[:10])
    if end:
        sql += " AND substr(start_date,1,10) <= ?"
        params.append(end[:10])
    sql += " ORDER BY start_date DESC"
    if limit:
        sql += f" LIMIT {limit}"

    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_stat_types() -> list[str]:
    """获取已导入的所有记录类型。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT type FROM health_records ORDER BY type"
        ).fetchall()
        return [r["type"] for r in rows]


def get_workout_types() -> list[str]:
    """获取已导入的训练类型。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT workout_activity_type FROM health_workouts ORDER BY workout_activity_type"
        ).fetchall()
        return [r["workout_activity_type"] for r in rows]


def get_import_time(cache=None):
    """Read a validated marker only from the supplied cache; never initialize storage."""
    marker = cache.get("_health_import_time") if cache is not None else None
    value = marker.get("time") if isinstance(marker, dict) else None
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def get_import_count(*, cache=None) -> dict:
    """Read health totals; callers may supply the active cache for the import marker."""
    with _conn() as conn:
        records = conn.execute(
            "SELECT type, COUNT(*) as cnt FROM health_records GROUP BY type ORDER BY cnt DESC"
        ).fetchall()
        workouts = conn.execute(
            "SELECT workout_activity_type as type, COUNT(*) as cnt FROM health_workouts GROUP BY workout_activity_type ORDER BY cnt DESC"
        ).fetchall()
        sleep_counts = conn.execute(
            "SELECT type, COUNT(*) as cnt FROM health_sleep GROUP BY type ORDER BY cnt DESC"
        ).fetchall()
    try:
        import_time = get_import_time(cache)
    except (OSError, sqlite3.Error):
        logger.warning("Unable to read health import marker", exc_info=True)
        import_time = None
    return {
        "records": [dict(r) for r in records],
        "workouts": [dict(r) for r in workouts],
        "sleep": [dict(r) for r in sleep_counts],
        "total_records": sum(r["cnt"] for r in records),
        "total_workouts": sum(r["cnt"] for r in workouts),
        "total_sleep": sum(r["cnt"] for r in sleep_counts),
        "health_import_time": import_time,
    }


def get_data_coverage() -> dict:
    """Return the earliest/latest imported dates across records, workouts and sleep."""
    with _conn() as conn:
        row = conn.execute(
            """SELECT MIN(first_date) AS start_date, MAX(last_date) AS end_date FROM (
                   SELECT (SELECT MIN(start_date) FROM health_records) AS first_date,
                          (SELECT MAX(start_date) FROM health_records) AS last_date
                   UNION ALL
                   SELECT (SELECT MIN(start_date) FROM health_workouts),
                          (SELECT MAX(start_date) FROM health_workouts)
                   UNION ALL
                   SELECT (SELECT MIN(start_date) FROM health_sleep),
                          (SELECT MAX(start_date) FROM health_sleep)
               )"""
        ).fetchone()
    return {
        "start_date": row["start_date"][:10] if row and row["start_date"] else None,
        "end_date": row["end_date"][:10] if row and row["end_date"] else None,
    }

def get_latest_dates(type_filter=None):
    """Get the latest start_date for each AH record type.
    Returns dict of type -> latest YYYY-MM-DD or None.
    If type_filter is given, returns only that type.
    """
    with _conn() as conn:
        if type_filter:
            row = conn.execute(
                "SELECT MAX(start_date) as latest FROM health_records WHERE type = ?",
                (type_filter,)
            ).fetchone()
            return {type_filter: row['latest'][:10] if row['latest'] else None}
        # Seek to the next distinct type, then its last timestamp. GROUP BY
        # would inspect every sample even though the primary key covers both.
        rows = conn.execute(
            """WITH RECURSIVE metric_types(type) AS (
                   SELECT MIN(type) FROM health_records
                   UNION ALL
                   SELECT (SELECT MIN(type) FROM health_records WHERE type > metric_types.type)
                   FROM metric_types WHERE type IS NOT NULL
               )
               SELECT type, (SELECT MAX(start_date) FROM health_records
                             WHERE type = metric_types.type) AS latest
               FROM metric_types WHERE type IS NOT NULL"""
        ).fetchall()
        return {r["type"]: r["latest"][:10] if r["latest"] is not None else None for r in rows}


def query_records_desc(type_filter: str = None,
                       start: str = None,
                       end: str = None,
                       limit: int = None) -> list[dict]:
    """查询健康记录，按日期倒序（最新在前）。"""
    sql = "SELECT * FROM health_records WHERE 1=1"
    params = []
    if type_filter:
        sql += " AND type = ?"
        params.append(type_filter)
    if start:
        sql += " AND substr(start_date,1,10) >= ?"
        params.append(start[:10])
    if end:
        sql += " AND substr(start_date,1,10) <= ?"
        params.append(end[:10])
    sql += " ORDER BY start_date DESC"
    if limit:
        sql += f" LIMIT {limit}"

    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def query_sleep(start: str = None,
                end: str = None,
                limit: int = None) -> list[dict]:
    """查询睡眠记录, 按日期倒序。"""
    sql = "SELECT * FROM health_sleep WHERE 1=1"
    params = []
    if start:
        sql += " AND substr(start_date,1,10) >= ?"
        params.append(start[:10])
    if end:
        sql += " AND substr(start_date,1,10) <= ?"
        params.append(end[:10])
    sql += " ORDER BY start_date DESC"
    if limit:
        sql += f" LIMIT {limit}"

    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_sleep_summary(start: str = None, end: str = None) -> list[dict]:
    """Summarize connected sleep intervals by completion date, without double counting."""
    from sleep_stats import parse_intervals, summarize_intervals, window_components

    def padded(day, delta):
        ordinal = date.fromisoformat(day[:10]).toordinal() + delta
        return date.fromordinal(max(date.min.toordinal(), min(date.max.toordinal(), ordinal))).isoformat()

    lower = padded(start, -2) if start else None
    upper = padded(end, 2) if end else None
    # Date-only SQL bounds accept XML and ISO separators. Padding covers every
    # valid UTC offset; exact absolute-time components then determine expansion.
    # Disconnected padding rows must not cause repeated outward expansion.
    with _conn() as conn:
        conn.execute("BEGIN")
        while True:
            sql, params = "SELECT type, start_date, end_date FROM health_sleep WHERE 1=1", []
            for column, bound, operator in (("end_date", lower, ">="), ("start_date", upper, "<=")):
                if bound:
                    # Unusual but valid ISO formats are parsed in Python rather
                    # than silently excluded by a canonical-date text bound.
                    sql += (f" AND (substr(trim({column}),1,10) {operator} ?"
                            f" OR substr(trim({column}),5,1) != '-'"
                            f" OR substr(trim({column}),8,1) != '-')")
                    params.append(bound)
            intervals = window_components(parse_intervals(conn.execute(sql, params)), start, end)
            if not intervals:
                return []
            first = min(interval.start[0].date().isoformat() for interval in intervals)
            last = max(interval.end[0].date().isoformat() for interval in intervals)
            new_lower = min(lower, padded(first, -2)) if lower else None
            new_upper = max(upper, padded(last, 2)) if upper else None
            if (lower, upper) == (new_lower, new_upper):
                break
            lower, upper = new_lower, new_upper
    return [row for row in summarize_intervals(intervals)
            if (not start or row["date"] >= start[:10]) and (not end or row["date"] <= end[:10])]
