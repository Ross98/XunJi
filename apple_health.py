"""
Apple Health 导出数据导入与查询模块。

从 Apple Health 导出 XML 中流式解析并缓存到 SQLite，
提供各类健康指标和训练数据的查询接口。
"""

import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional


HEALTH_DB_PATH = Path(__file__).parent / "apple_health_cache.sqlite"
# Apple Health 导出目录
EXPORT_DIR = Path(__file__).parent / "Apple Health" / "apple_health_export"
EXPORT_XML = EXPORT_DIR / "导出.xml"


def _conn():
    conn = sqlite3.connect(str(HEALTH_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


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
        """)


# ---------- 导入 ----------

def import_all(progress_callback=None, batch_size=5000):
    """流式导入导出.xml 全部数据到 SQLite (增量: 跳过已有记录)"""
    _init_db()
    xml_path = str(EXPORT_XML)
    if not Path(xml_path).exists():
        raise FileNotFoundError(f"未找到 Apple Health 导出文件: {xml_path}")

    # 获取已有数据的最新时间戳，跳过旧记录
    with _conn() as conn:
        last_rec = conn.execute("SELECT MAX(start_date) FROM health_records").fetchone()[0]
        last_wo = conn.execute("SELECT MAX(start_date) FROM health_workouts").fetchone()[0]
        last_sleep = conn.execute("SELECT MAX(start_date) FROM health_sleep").fetchone()[0]

    # 已知最大 start_date，只解析更新的
    max_known = max([v for v in (last_rec, last_wo, last_sleep) if v] or ["1970-01-01"])
    # 清理历史缓存标记
    IMPORTER_CACHE = {}

    total = 0
    skipped = 0
    record_batch = []
    workout_batch = []
    sleep_batch = []

    for event, elem in ET.iterparse(xml_path, events=("end",)):
        if elem.tag == "Record":
            t = elem.get("type", "")
            sd = elem.get("startDate", "")
            if sd <= max_known:
                elem.clear()
                skipped += 1
                continue
            if t == "HKCategoryTypeIdentifierSleepAnalysis":
                rec = _parse_sleep(elem)
                if rec:
                    sleep_batch.append(rec)
            else:
                rec = _parse_record(elem)
                if rec:
                    record_batch.append(rec)
        elif elem.tag == "Workout":
            sd = elem.get("startDate", "")
            if sd <= max_known:
                elem.clear()
                skipped += 1
                continue
            wo = _parse_workout(elem)
            if wo:
                workout_batch.append(wo)

        elem.clear()

        if len(record_batch) >= batch_size or len(workout_batch) >= batch_size or len(sleep_batch) >= batch_size:
            total += len(record_batch) + len(workout_batch) + len(sleep_batch)
            _bulk_insert(record_batch, workout_batch, sleep_batch)
            record_batch.clear()
            workout_batch.clear()
            sleep_batch.clear()
            if progress_callback:
                progress_callback(total)

    if record_batch or workout_batch or sleep_batch:
        total += len(record_batch) + len(workout_batch) + len(sleep_batch)
        _bulk_insert(record_batch, workout_batch, sleep_batch)

    return total


def _parse_record(elem):
    try:
        val = elem.get("value")
        return {
            "type": elem.get("type", ""),
            "start_date": elem.get("startDate", ""),
            "end_date": elem.get("endDate", ""),
            "value": float(val) if val else None,
            "unit": elem.get("unit", ""),
            "source_name": elem.get("sourceName", ""),
        }
    except (ValueError, TypeError):
        return None


def _parse_workout(elem):
    dist_raw = elem.get("totalDistance")
    cal_raw = elem.get("totalEnergyBurned")
    try:
        return {
            "workout_activity_type": elem.get("workoutActivityType", ""),
            "start_date": elem.get("startDate", ""),
            "end_date": elem.get("endDate", ""),
            "duration": float(elem.get("duration", 0)) if elem.get("duration") else None,
            "duration_unit": elem.get("durationUnit", ""),
            "total_distance": float(dist_raw) if dist_raw else None,
            "total_distance_unit": elem.get("totalDistanceUnit", ""),
            "total_energy_burned": float(cal_raw) if cal_raw else None,
            "total_energy_burned_unit": elem.get("totalEnergyBurnedUnit", ""),
            "source_name": elem.get("sourceName", ""),
        }
    except (ValueError, TypeError):
        return None


def _parse_sleep(elem):
    """解析睡眠 CategoryRecord。value 是 HKCategoryValueSleepAnalysis* 枚举。"""
    return {
        "type": elem.get("value", ""),
        "start_date": elem.get("startDate", ""),
        "end_date": elem.get("endDate", ""),
        "source_name": elem.get("sourceName", ""),
    }


def _bulk_insert(records, workouts, sleep_records=None):
    with _conn() as conn:
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
        params.append(start)
    if end:
        sql += " AND substr(start_date,1,10) <= ?"
        params.append(end)
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
        params.append(start)
    if end:
        sql += " AND substr(start_date,1,10) <= ?"
        params.append(end)
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


def get_import_count() -> dict:
    """返回各类型数据计数。"""
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
    return {
        "records": [dict(r) for r in records],
        "workouts": [dict(r) for r in workouts],
        "sleep": [dict(r) for r in sleep_counts],
        "total_records": sum(r["cnt"] for r in records),
        "total_workouts": sum(r["cnt"] for r in workouts),
    }

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
        params.append(start)
    if end:
        sql += " AND substr(start_date,1,10) <= ?"
        params.append(end)
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
        params.append(start)
    if end:
        sql += " AND substr(start_date,1,10) <= ?"
        params.append(end)
    sql += " ORDER BY start_date DESC"
    if limit:
        sql += f" LIMIT {limit}"

    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_sleep_summary(start: str = None, end: str = None) -> list[dict]:
    """按天汇总睡眠各阶段时长 (分钟)。Python 计算 (SQLite julianday 不支持 +0800 时区)。"""
    from datetime import datetime

    sql = "SELECT * FROM health_sleep WHERE 1=1"
    params = []
    if start:
        sql += " AND substr(start_date,1,10) >= ?"
        params.append(start)
    if end:
        sql += " AND substr(end_date,1,10) <= ?"
        params.append(end)
    sql += " ORDER BY start_date"

    def _parse_dt(s: str) -> datetime:
        # "2026-07-10 07:19:15 +0800" -> datetime
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")

    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    by_date = {}
    for r in rows:
        try:
            start_dt = _parse_dt(r["start_date"])
            end_dt = _parse_dt(r["end_date"])
            minutes = (end_dt - start_dt).total_seconds() / 60
            if minutes < 0:
                continue
        except (ValueError, TypeError):
            continue

        # 归类到开始日期
        d = r["start_date"][:10]
        if d not in by_date:
            by_date[d] = {"date": d}
        t = r["type"].replace("HKCategoryValueSleepAnalysis", "")
        by_date[d][t] = by_date[d].get(t, 0) + int(minutes + 0.5)

    result = sorted(by_date.values(), key=lambda x: x["date"], reverse=True)
    return result
