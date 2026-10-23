"""Apple Health 数据路由"""

import asyncio
from contextlib import suppress
from datetime import date, datetime, timedelta, timezone
import threading
import logging
import stat

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.concurrency import run_in_threadpool
from app_state import get_cache, get_jinja_env
from data_service import DataFreshnessService
from sleep_stats import confirmed_sleep_minutes
import apple_health as ah
import hozz_sync as hs
from health_values import health_number as _health_number, metric_records_view

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)
SYNC_INTERVAL_SECONDS = 60
_import_state_lock = threading.Lock()
_import_state = {
    "status": "idle",
    "kind": None,
    "processed": 0,
    "total": None,
    "message": "",
    "started_at": None,
    "finished_at": None,
}
_import_task = None
_sync_watch_task = None
_import_cancel_event = threading.Event()

# 健康中英映射
HEALTH_LABELS = {
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "HRV (ms)",
    "HKQuantityTypeIdentifierRestingHeartRate": "静息心率 (bpm)",
    "HKQuantityTypeIdentifierVO2Max": "VO₂ Max (ml/kg/min)",
    "HKQuantityTypeIdentifierBodyMass": "体重 (kg)",
    "HKQuantityTypeIdentifierBodyFatPercentage": "体脂率 (%)",
    "HKQuantityTypeIdentifierHeartRate": "心率",
    "HKQuantityTypeIdentifierActiveEnergyBurned": "活动能量",
    "HKQuantityTypeIdentifierBasalEnergyBurned": "基础能量",
    "HKQuantityTypeIdentifierPhysicalEffort": "体力负荷",
    "HKQuantityTypeIdentifierDistanceWalkingRunning": "步行与跑步距离",
    "HKQuantityTypeIdentifierStepCount": "步数",
    "HKQuantityTypeIdentifierRespiratoryRate": "呼吸频率",
    "HKQuantityTypeIdentifierAppleStandTime": "站立时间",
    "HKQuantityTypeIdentifierAppleExerciseTime": "锻炼时间",
    "HKQuantityTypeIdentifierOxygenSaturation": "血氧饱和度",
    "HKQuantityTypeIdentifierWalkingSpeed": "步行速度",
    "HKQuantityTypeIdentifierWalkingStepLength": "步幅",
    "HKQuantityTypeIdentifierWalkingDoubleSupportPercentage": "双脚支撑时间占比",
    "HKQuantityTypeIdentifierWalkingAsymmetryPercentage": "步态不对称",
    "HKQuantityTypeIdentifierAppleWalkingSteadiness": "步行稳定性",
    "HKQuantityTypeIdentifierDistanceCycling": "骑行距离",
    "HKQuantityTypeIdentifierTimeInDaylight": "日照时间",
    "HKQuantityTypeIdentifierStairDescentSpeed": "下楼速度",
    "HKQuantityTypeIdentifierStairAscentSpeed": "上楼速度",
    "HKQuantityTypeIdentifierFlightsClimbed": "爬楼层数",
    "HKQuantityTypeIdentifierWalkingHeartRateAverage": "步行平均心率",
    "HKQuantityTypeIdentifierEnvironmentalAudioExposure": "环境噪声暴露",
    "HKQuantityTypeIdentifierBodyMassIndex": "BMI",
    "HKQuantityTypeIdentifierHeadphoneAudioExposure": "耳机音量暴露",
    "HKQuantityTypeIdentifierWorkoutEffortScore": "训练负荷评分",
    "HKQuantityTypeIdentifierLeanBodyMass": "瘦体重",
    "HKQuantityTypeIdentifierSixMinuteWalkTestDistance": "六分钟步行距离",
    "HKQuantityTypeIdentifierAppleSleepingBreathingDisturbances": "睡眠呼吸干扰",
    "HKQuantityTypeIdentifierEstimatedWorkoutEffortScore": "估算训练负荷评分",
    "HKQuantityTypeIdentifierRunningSpeed": "跑步速度",
    "HKQuantityTypeIdentifierAppleSleepingWristTemperature": "睡眠腕温",
    "HKQuantityTypeIdentifierHeartRateRecoveryOneMinute": "一分钟心率恢复",
    "HKQuantityTypeIdentifierRunningPower": "跑步功率",
    "HKQuantityTypeIdentifierEnvironmentalSoundReduction": "环境降噪",
    "HKQuantityTypeIdentifierRunningVerticalOscillation": "跑步垂直振幅",
    "HKQuantityTypeIdentifierHeight": "身高",
    "HKQuantityTypeIdentifierRunningGroundContactTime": "跑步触地时间",
    "HKQuantityTypeIdentifierRunningStrideLength": "跑步步幅",
    "HKDataTypeSleepDurationGoal": "睡眠时长目标",
}

WORKOUT_LABELS = {
    "HKWorkoutActivityTypeWalking": "步行",
    "HKWorkoutActivityTypeFunctionalStrengthTraining": "功能性力量训练",
    "HKWorkoutActivityTypeTraditionalStrengthTraining": "传统力量训练",
    "HKWorkoutActivityTypeHiking": "远足",
    "HKWorkoutActivityTypeCycling": "骑行",
    "HKWorkoutActivityTypeRunning": "跑步",
    "HKWorkoutActivityTypeCoreTraining": "核心训练",
    "HKWorkoutActivityTypeStairs": "楼梯训练",
    "HKWorkoutActivityTypeMixedCardio": "混合有氧",
}


def health_label(identifier: str) -> str:
    return HEALTH_LABELS.get(
        identifier,
        identifier.replace("HKQuantityTypeIdentifier", "")
        .replace("HKCategoryTypeIdentifier", ""),
    )


def workout_label(identifier: str) -> str:
    return WORKOUT_LABELS.get(
        identifier, identifier.replace("HKWorkoutActivityType", "")
    )


def _reset_import_state_for_tests(status: str = "idle"):
    global _import_task
    with _import_state_lock:
        _import_state.update({
            "status": status,
            "kind": None,
            "processed": 0,
            "total": None,
            "message": "",
            "started_at": None,
            "finished_at": None,
        })
    _import_task = None
    _import_cancel_event.clear()


def _set_import_state(**updates):
    with _import_state_lock:
        _import_state.update(updates)


def _xml_export_info():
    """Inspect the source once; directories and unavailable files are not exports."""
    try:
        info = ah.EXPORT_XML.stat()
        available = stat.S_ISREG(info.st_mode)
        return {"available": available, "size_mb": round(info.st_size / 1024 / 1024, 1) if available else None}
    except OSError:
        return {"available": False, "size_mb": None}


async def _run_import_worker(func, **kwargs):
    """Keep the thread joined even when its owning asyncio task is cancelled."""
    worker = asyncio.create_task(run_in_threadpool(func, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Cancelling run_in_threadpool's asyncio wrapper cannot stop the thread.
        # Its cancellation event must be observed before shared resources close.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()  # Consume failures; shutdown retains cancellation.
        raise


async def _run_import_job():
    def progress(processed):
        _set_import_state(processed=processed, message="正在解析并写入新记录")

    try:
        total = await _run_import_worker(
            ah.import_all,
            cache=get_cache(),
            progress_callback=progress,
            cancel_event=_import_cancel_event,
        )
        _set_import_state(
            status="ok",
            total=total,
            processed=total,
            message="导入完成",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    except ah.ImportCancelled:
        _set_import_state(
            status="cancelled",
            message="导入已停止",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    except FileNotFoundError as exc:
        _set_import_state(
            status="error",
            message=str(exc),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        _set_import_state(
            status="error",
            message=f"导入失败: {exc}",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )


def _health_metric_records(metric, start, end):
    # Keep missing sample positions and dates; only sanitize the display copy.
    return metric_records_view(ah.query_records_desc(metric, start, end, limit=30))


def _health_numeric_rows(records, fields):
    """Copy measurement fields into JSON-safe values without changing stored rows."""
    return [dict(row, **{field: _health_number(row[field]) for field in fields if field in row})
            for row in records]


def _format_health_value(records, precision=0, scale=1):
    value = _health_number(records[0].get("value"), scale) if records else None
    return f"{value:.{precision}f}" if value is not None else "--"


def _build_body_table(body_mass: list[dict], body_fat: list[dict], max_rows: int = 5) -> list[dict]:
    """预生成身体数据表格行，避免 Jinja2 中 O(n²) 的 selectattr。"""
    by_date: dict[str, dict] = {}
    for r in body_mass:
        d = r["start_date"][:10]
        if d not in by_date:
            by_date[d] = {"date": d}
        weight = _health_number(r.get("value"))
        if "weight" not in by_date[d] and weight is not None:
            by_date[d]["weight"] = round(weight, 2)
    for r in body_fat:
        d = r["start_date"][:10]
        if d not in by_date:
            by_date[d] = {"date": d}
        bf = _health_number(r.get("value"), 100)
        if "bodyfat" not in by_date[d] and bf is not None:
            by_date[d]["bodyfat"] = round(bf, 1)  # 0.19 → 19%

    rows = sorted(by_date.values(), key=lambda x: x["date"], reverse=True)
    return rows[:max_rows]


@router.get("/health", response_class=HTMLResponse)
async def health_page(request: Request):
    """健康数据总览页面"""
    return await run_in_threadpool(_render_health_page, request)


def _render_health_page(request: Request):
    """Read SQLite and render together without blocking other requests."""
    env = get_jinja_env()
    tmpl = env.get_template("health.html")
    export = _xml_export_info()
    stats = ah.get_import_count()
    imported = any(stats[key] > 0 for key in ("total_records", "total_workouts", "total_sleep"))

    end = date.today().isoformat()
    start = (date.today() - timedelta(days=90)).isoformat()

    hrv = _health_metric_records("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", start, end)
    resting_hr = _health_metric_records("HKQuantityTypeIdentifierRestingHeartRate", start, end)
    vo2 = _health_metric_records("HKQuantityTypeIdentifierVO2Max", start, end)
    respiratory = _health_metric_records("HKQuantityTypeIdentifierRespiratoryRate", start, end)
    oxygen = _health_metric_records("HKQuantityTypeIdentifierOxygenSaturation", start, end)
    wrist_temperature = _health_metric_records("HKQuantityTypeIdentifierAppleSleepingWristTemperature", start, end)
    heart_recovery = _health_metric_records("HKQuantityTypeIdentifierHeartRateRecoveryOneMinute", start, end)
    walking_speed = _health_metric_records("HKQuantityTypeIdentifierWalkingSpeed", start, end)
    walking_step_length = _health_metric_records("HKQuantityTypeIdentifierWalkingStepLength", start, end)
    walking_asymmetry = _health_metric_records("HKQuantityTypeIdentifierWalkingAsymmetryPercentage", start, end)
    walking_steadiness = _health_metric_records("HKQuantityTypeIdentifierAppleWalkingSteadiness", start, end)
    body_mass = _health_metric_records("HKQuantityTypeIdentifierBodyMass", start, end)
    body_fat = _health_metric_records("HKQuantityTypeIdentifierBodyFatPercentage", start, end)
    workouts = ah.query_workouts(start=start, end=end, limit=5)

    body_rows = _build_body_table(body_mass, body_fat, max_rows=5)
    total_body_dates = len(set(r["start_date"][:10] for r in body_mass) | set(r["start_date"][:10] for r in body_fat))

    sleep_summary = ah.get_sleep_summary(start, end)
    sleep_count = sum(row["record_count"] for row in sleep_summary)
    latest_sleep = sleep_summary[0] if sleep_summary else None
    sleep_nights = len(sleep_summary)
    sleep_metrics = None
    asleep_total = confirmed_sleep_minutes(latest_sleep)
    if asleep_total is not None:
        sleep_metrics = {
            "date": latest_sleep.get("date"),
            "hours": round(asleep_total / 60, 1) if asleep_total else 0,
            "deep_pct": round((latest_sleep.get("AsleepDeep", 0) or 0) / asleep_total * 100) if asleep_total else 0,
            "rem_pct": round((latest_sleep.get("AsleepREM", 0) or 0) / asleep_total * 100) if asleep_total else 0,
            "core_pct": round((latest_sleep.get("AsleepCore", 0) or 0) / asleep_total * 100) if asleep_total else 0,
            "awake_minutes": latest_sleep.get("Awake", 0) or 0,
        }

    coverage = ah.get_data_coverage()

    freshness_svc = DataFreshnessService(xunji_cache=get_cache())
    freshness_ctx = freshness_svc.get_freshness_context()
    return HTMLResponse(tmpl.render(
        request=request,
        freshness=freshness_ctx,
        imported=imported,
        stats=stats,
        hrv=hrv,
        resting_hr=resting_hr,
        vo2=vo2,
        respiratory=respiratory,
        oxygen=oxygen,
        wrist_temperature=wrist_temperature,
        heart_recovery=heart_recovery,
        walking_speed=walking_speed,
        walking_step_length=walking_step_length,
        walking_asymmetry=walking_asymmetry,
        walking_steadiness=walking_steadiness,
        body_rows=body_rows,
        body_row_count=total_body_dates,
        workouts=workouts,
        sleep_summary=sleep_summary,
        latest_sleep=latest_sleep,
        sleep_nights=sleep_nights,
        sleep_metrics=sleep_metrics,
        sleep_count=sleep_count,
        health_label=health_label,
        health_value=_format_health_value,
        workout_label=workout_label,
        export_path=str(ah.EXPORT_XML),
        export_exists=export["available"],
        export_size_mb=export["size_mb"],
        coverage=coverage,
        hozz=_hozz_status_context(),
        database_size_mb=round(ah.HEALTH_DB_PATH.stat().st_size / 1024 / 1024, 1) if ah.HEALTH_DB_PATH.exists() else 0,
        today=date.today(),
    ))


@router.post("/health/import")
async def trigger_import():
    global _import_task
    with _import_state_lock:
        if _import_state["status"] == "running":
            return {"status": "busy", "message": "Apple Health 数据正在导入"}
    export = await run_in_threadpool(_xml_export_info)
    with _import_state_lock:
        if _import_state["status"] == "running":
            return {"status": "busy", "message": "Apple Health 数据正在导入"}
        if not export["available"]:
            return {"status": "error", "export_available": False,
                    "message": "未找到 Apple Health XML 导出文件，请放置文件后刷新页面重试"}
        _import_state.update({
            "status": "running",
            "kind": "xml",
            "processed": 0,
            "total": None,
            "message": "准备读取 Apple Health 导出文件",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        })
    _import_cancel_event.clear()
    _import_task = asyncio.create_task(_run_import_job())
    return {"status": "started", "message": "Apple Health 数据已在后台开始导入"}


@router.get("/health/import/status")
async def import_status():
    export = await run_in_threadpool(_xml_export_info)
    with _import_state_lock:
        return dict(_import_state, export_available=export["available"])


async def _run_hozz_job():
    try:
        result = await _run_import_worker(
            hs.sync, cache=get_cache(), cancel_event=_import_cancel_event)
        if result["status"] == "cancelled":
            status, message = "cancelled", "Hozz 同步已停止"
        elif result["status"] == "ok":
            status, message = "ok", "Hozz 同步完成"
        else:
            status = "error"
            message = "；".join(result["errors"]) or "Hozz 同步未完成"
        _set_import_state(status=status,
                          total=result["inserted"], processed=result["inserted"],
                          message=message,
                          finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        _set_import_state(status="error", message=f"Hozz 同步失败: {exc}",
                          finished_at=datetime.now(timezone.utc).isoformat())


@router.post("/health/sync")
async def trigger_hozz_sync():
    global _import_task
    with _import_state_lock:
        if _import_state["status"] == "running":
            return {"status": "busy", "message": "Apple Health 数据正在导入"}
        _import_state.update(status="running", kind="hozz", processed=0, total=None,
                             message="正在检查 iCloud Hozz 新批次",
                             started_at=datetime.now(timezone.utc).isoformat(), finished_at=None)
    _import_cancel_event.clear()
    _import_task = asyncio.create_task(_run_hozz_job())
    return {"status": "started", "message": "Hozz 同步已开始"}


@router.get("/health/sync/status")
async def hozz_sync_status():
    return await run_in_threadpool(_hozz_status_context)


def _hozz_status_context():
    result = hs.status()
    result["watcher_running"] = _sync_watch_task is not None and not _sync_watch_task.done()
    return result


async def start_health_background_tasks():
    global _sync_watch_task
    if _sync_watch_task is not None and not _sync_watch_task.done():
        return

    async def watch():
        while True:
            try:
                await trigger_hozz_sync()
            except Exception:
                logger.exception("Unable to start automatic Hozz check; will retry")
                _set_import_state(status="error", message="自动检查启动失败，将在下一轮重试",
                                  finished_at=datetime.now(timezone.utc).isoformat())
            await asyncio.sleep(SYNC_INTERVAL_SECONDS)

    _sync_watch_task = asyncio.create_task(watch())


async def stop_health_background_tasks() -> None:
    """Signal the importer to stop, cancel its wrapper, and join its worker."""
    global _import_task, _sync_watch_task
    watcher = _sync_watch_task
    _sync_watch_task = None
    if watcher is not None:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
    _import_cancel_event.set()
    task = _import_task
    _import_task = None
    if task is None or task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    _set_import_state(
        status="cancelled",
        message="导入已停止",
        finished_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/health/data")
async def health_data(
    metric: str = Query("HKQuantityTypeIdentifierHeartRateVariabilitySDNN"),
    start: str = Query(None),
    end: str = Query(None),
    limit: int = Query(100, ge=1, le=1000),
):
    records = await run_in_threadpool(ah.query_records, metric, start, end, limit)
    return {"data": _health_numeric_rows(records, ("value",))}


@router.get("/health/workouts")
async def health_workouts(
    activity: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    limit: int = Query(50, ge=1, le=1000),
):
    recs = await run_in_threadpool(ah.query_workouts, activity, start, end, limit)
    return {"data": _health_numeric_rows(recs, ("duration", "total_distance", "total_energy_burned"))}
