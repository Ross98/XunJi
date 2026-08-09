"""Apple Health 数据路由"""

import asyncio
from contextlib import suppress
from datetime import date, datetime, timedelta, timezone
import threading

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.concurrency import run_in_threadpool
from app_state import get_cache, get_jinja_env
from data_service import DataFreshnessService
import apple_health as ah

router = APIRouter(tags=["health"])
_import_state_lock = threading.Lock()
_import_state = {
    "status": "idle",
    "processed": 0,
    "total": None,
    "message": "",
    "started_at": None,
    "finished_at": None,
}
_import_task = None
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


async def _run_import_job():
    def progress(processed):
        _set_import_state(processed=processed, message="正在解析并写入新记录")

    try:
        total = await run_in_threadpool(
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


def _build_body_table(body_mass: list[dict], body_fat: list[dict], max_rows: int = 5) -> list[dict]:
    """预生成身体数据表格行，避免 Jinja2 中 O(n²) 的 selectattr。"""
    by_date: dict[str, dict] = {}
    for r in body_mass:
        d = r["start_date"][:10]
        if d not in by_date:
            by_date[d] = {"date": d}
        by_date[d]["weight"] = round(r["value"], 2) if r.get("value") else None
    for r in body_fat:
        d = r["start_date"][:10]
        if d not in by_date:
            by_date[d] = {"date": d}
        bf = r["value"]
        if bf is not None:
            by_date[d]["bodyfat"] = round(bf * 100, 1)  # 0.19 → 19%

    rows = sorted(by_date.values(), key=lambda x: x["date"], reverse=True)
    return rows[:max_rows]


@router.get("/health", response_class=HTMLResponse)
async def health_page(request: Request):
    """健康数据总览页面"""
    env = get_jinja_env()
    tmpl = env.get_template("health.html")
    stats = ah.get_import_count()
    imported = stats["total_records"] > 0 or stats["total_workouts"] > 0

    end = date.today().isoformat()
    start = (date.today() - timedelta(days=90)).isoformat()

    hrv = ah.query_records_desc("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", start, end, limit=30)
    resting_hr = ah.query_records_desc("HKQuantityTypeIdentifierRestingHeartRate", start, end, limit=30)
    vo2 = ah.query_records_desc("HKQuantityTypeIdentifierVO2Max", start, end, limit=30)
    respiratory = ah.query_records_desc("HKQuantityTypeIdentifierRespiratoryRate", start, end, limit=30)
    oxygen = ah.query_records_desc("HKQuantityTypeIdentifierOxygenSaturation", start, end, limit=30)
    wrist_temperature = ah.query_records_desc("HKQuantityTypeIdentifierAppleSleepingWristTemperature", start, end, limit=30)
    heart_recovery = ah.query_records_desc("HKQuantityTypeIdentifierHeartRateRecoveryOneMinute", start, end, limit=30)
    walking_speed = ah.query_records_desc("HKQuantityTypeIdentifierWalkingSpeed", start, end, limit=30)
    walking_step_length = ah.query_records_desc("HKQuantityTypeIdentifierWalkingStepLength", start, end, limit=30)
    walking_asymmetry = ah.query_records_desc("HKQuantityTypeIdentifierWalkingAsymmetryPercentage", start, end, limit=30)
    walking_steadiness = ah.query_records_desc("HKQuantityTypeIdentifierAppleWalkingSteadiness", start, end, limit=30)
    body_mass = ah.query_records_desc("HKQuantityTypeIdentifierBodyMass", start, end, limit=30)
    body_fat = ah.query_records_desc("HKQuantityTypeIdentifierBodyFatPercentage", start, end, limit=30)
    workouts = ah.query_workouts(start=start, end=end, limit=5)

    body_rows = _build_body_table(body_mass, body_fat, max_rows=5)
    total_body_dates = len(set(r["start_date"][:10] for r in body_mass) | set(r["start_date"][:10] for r in body_fat))

    sleep_summary = ah.get_sleep_summary(start, end)
    sleep_count = len(ah.query_sleep(start, end)) if imported else 0
    latest_sleep = sleep_summary[0] if sleep_summary else None
    sleep_nights = len(sleep_summary)
    sleep_metrics = None
    if latest_sleep:
        asleep_total = sum(
            latest_sleep.get(key, 0) or 0
            for key in ("AsleepUnspecified", "AsleepCore", "AsleepREM", "AsleepDeep")
        )
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
        workout_label=workout_label,
        export_path=str(ah.EXPORT_XML),
        export_exists=ah.EXPORT_XML.exists(),
        export_size_mb=round(ah.EXPORT_XML.stat().st_size / 1024 / 1024, 1) if ah.EXPORT_XML.exists() else None,
        coverage=coverage,
        database_size_mb=round(ah.HEALTH_DB_PATH.stat().st_size / 1024 / 1024, 1) if ah.HEALTH_DB_PATH.exists() else 0,
        today=date.today(),
    ))


@router.post("/health/import")
async def trigger_import():
    global _import_task
    with _import_state_lock:
        if _import_state["status"] == "running":
            return {"status": "busy", "message": "Apple Health 数据正在导入"}
        _import_state.update({
            "status": "running",
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
    with _import_state_lock:
        return dict(_import_state)


async def stop_health_background_tasks() -> None:
    """Signal the importer to stop, then cancel its asyncio wrapper."""
    global _import_task
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
    metric: str = Query("HeartRateVariabilitySDNN"),
    start: str = Query(None),
    end: str = Query(None),
    limit: int = Query(100),
):
    records = ah.query_records(metric, start, end, limit)
    return {"data": records}


@router.get("/health/workouts")
async def health_workouts(
    activity: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    limit: int = Query(50),
):
    recs = ah.query_workouts(activity, start, end, limit)
    return {"data": recs}
