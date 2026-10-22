"""训练规划路由"""
from datetime import date, datetime, timedelta
import json
import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app_state import get_jinja_env, get_cache
from analysis import summarize_training
from data_service import extract_trains, get_cached_training
from planner import DEFAULT_PLAN_PREFERENCES, generate_plan, normalize_plan_preferences
from movements_cn import apply_movement_cn_to_trains

router = APIRouter(tags=["plan"])
logger = logging.getLogger(__name__)
PLAN_CONFIG_PATH = Path(__file__).resolve().parent.parent / "plan_config.json"


def _load_plan_preferences() -> dict:
    preferences = dict(DEFAULT_PLAN_PREFERENCES)
    if PLAN_CONFIG_PATH.exists():
        try:
            saved = json.loads(PLAN_CONFIG_PATH.read_text(encoding="utf-8"))
            return normalize_plan_preferences(saved)
        except (OSError, ValueError, TypeError):
            logger.exception("Unable to load plan preferences")
    return preferences


def _save_plan_preferences(preferences: dict):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=PLAN_CONFIG_PATH.parent,
            prefix=".plan-", suffix=".tmp", delete=False,
        ) as target:
            temporary = Path(target.name)
            json.dump(preferences, target, ensure_ascii=False, indent=2)
        os.replace(temporary, PLAN_CONFIG_PATH)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _current_sleep_stages(records: list[dict], today: str) -> dict:
    """Use today's sleep and connected pre-midnight stages, excluding older nights."""
    intervals = []
    for record in records:
        try:
            start = datetime.fromisoformat(record["start_date"][:19])
            end = datetime.fromisoformat(record["end_date"][:19])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start and end.date().isoformat() <= today:
            intervals.append((end, start, record["type"]))

    stages = {}
    boundary = None
    for end, start, kind in sorted(intervals, reverse=True):
        if end.date().isoformat() != today and (boundary is None or end < boundary):
            continue
        boundary = min(boundary, start) if boundary else start
        stage = kind.removeprefix("HKCategoryValueSleepAnalysis")
        stages[stage] = stages.get(stage, 0) + (end - start).total_seconds() / 60
    return stages


@router.get("/plan", response_class=HTMLResponse)
async def plan_page(request: Request):
    today = date.today().isoformat()

    # Fetch last 30 days of training from cache
    cache = get_cache()
    recent_trains = []
    for i in range(30):
        d = (date.today() - timedelta(days=i)).isoformat()
        cached = get_cached_training(cache, d)
        if cached:
            trains = extract_trains(cached)
            recent_trains.extend(trains)

    recent_trains = apply_movement_cn_to_trains(recent_trains)

    summary = summarize_training(recent_trains)

    # Apple Health recovery context
    recovery = {}
    try:
        import apple_health as ah
        if ah.HEALTH_DB_PATH.exists():
            start = (date.today() - timedelta(days=30)).isoformat()

            # Sleep
            # Count sleep ending today, including stages spanning midnight.
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            sl = _current_sleep_stages(ah.query_sleep(yesterday, today), today)
            if sl:
                st = sl.get("AsleepUnspecified", 0) + sl.get("AsleepCore", 0) + sl.get("AsleepREM", 0) + sl.get("AsleepDeep", 0)
                if st > 0:
                    recovery["sleep_hours"] = round(st / 60, 1)
                    recovery["sleep_deep_pct"] = round(sl.get("AsleepDeep", 0) / st * 100, 0)
                    recovery["sleep_rem_pct"] = round(sl.get("AsleepREM", 0) / st * 100, 0)
                    recovery["sleep_core_pct"] = round(sl.get("AsleepCore", 0) / st * 100, 0)
                    recovery["sleep_awake"] = sl.get("Awake", 0)

            # HRV
            hrv = ah.query_records("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", start, today)
            current_hrv = [r for r in hrv if r["start_date"][:10] == today and r.get("value")]
            if current_hrv:
                recovery["hrv"] = round(current_hrv[-1]["value"], 0)
                # 30d avg
                vals = [r["value"] for r in hrv if r.get("value")]
                if vals:
                    recovery["hrv_avg"] = round(sum(vals) / len(vals), 0)

            # Resting HR
            rhr = ah.query_records("HKQuantityTypeIdentifierRestingHeartRate", start, today)
            current_rhr = [r for r in rhr if r["start_date"][:10] == today and r.get("value")]
            if current_rhr:
                recovery["rhr"] = round(current_rhr[-1]["value"], 0)
                rhr_values = [record["value"] for record in rhr if record.get("value")]
                if rhr_values:
                    recovery["rhr_avg"] = round(sum(rhr_values) / len(rhr_values), 0)

            # Activity
            today_str = date.today().isoformat()
            steps = ah.query_records("HKQuantityTypeIdentifierStepCount", today_str, today_str + " 23:59:59")
            if steps:
                recovery["steps"] = round(sum(s["value"] for s in steps if s.get("value")))
    except Exception:
        logger.exception("Apple Health recovery context unavailable")

    preferences = _load_plan_preferences()
    plan = generate_plan(
        recent_trains,
        reference_date=date.today(),
        preferences=preferences,
        recovery=recovery,
    )

    env = get_jinja_env()
    tmpl = env.get_template("plan.html")
    return HTMLResponse(tmpl.render(
        request=request,
        plan=plan,
        summary=summary,
        recovery=recovery,
        preferences=preferences,
        today=today,
        days=30,
    ))


@router.post("/plan/settings")
async def update_plan_settings(data: dict):
    preferences = _load_plan_preferences()
    for key in preferences:
        if key in data:
            preferences[key] = data[key]
    try:
        preferences = normalize_plan_preferences(preferences, strict=True)
    except ValueError:
        return {"status": "error", "message": "训练偏好格式不正确：天数和时长需为数字，其余项目需为文字"}
    try:
        _save_plan_preferences(preferences)
    except OSError:
        logger.exception("Unable to save plan preferences")
        return {"status": "error", "message": "训练偏好保存失败，请检查文件权限或磁盘空间后重试"}
    return {"status": "ok", "preferences": preferences}
