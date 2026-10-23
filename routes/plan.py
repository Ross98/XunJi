"""训练规划路由"""
from datetime import date, timedelta
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app_state import get_jinja_env, get_cache
from analysis import summarize_training
from data_service import extract_trains, get_cached_training
from planner import DEFAULT_PLAN_PREFERENCES, generate_plan, normalize_plan_preferences
from movements_cn import apply_movement_cn_to_trains
from local_settings import write_json_atomic
from sleep_stats import confirmed_sleep_minutes
from health_values import metric_records_view, health_mean

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
    write_json_atomic(PLAN_CONFIG_PATH, preferences)


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
            start = (date.today() - timedelta(days=29)).isoformat()

            # Sleep
            # Count sleep ending today, including stages spanning midnight.
            sleep_rows = ah.get_sleep_summary(today, today)
            sl = sleep_rows[0] if sleep_rows else {}
            st = confirmed_sleep_minutes(sl)
            recovery["sleep_conflict"] = bool(sl.get("Conflict"))
            if st is not None:
                recovery["sleep_hours"] = round(st / 60, 1)
                recovery["sleep_deep_pct"] = round(sl.get("AsleepDeep", 0) / st * 100, 0)
                recovery["sleep_rem_pct"] = round(sl.get("AsleepREM", 0) / st * 100, 0)
                recovery["sleep_core_pct"] = round(sl.get("AsleepCore", 0) / st * 100, 0)
                recovery["sleep_awake"] = sl.get("Awake", 0)

            for key, metric in (
                ("hrv", "HKQuantityTypeIdentifierHeartRateVariabilitySDNN"),
                ("rhr", "HKQuantityTypeIdentifierRestingHeartRate"),
            ):
                samples = metric_records_view(ah.query_records(metric, start, today))
                current = [row for row in samples if row["start_date"][:10] == today]
                # Keep the latest missing sample missing; do not revive an earlier value.
                if current and current[-1]["value"] is not None:
                    recovery[key] = round(current[-1]["value"], 0)
                    mean = health_mean(row["value"] for row in samples)
                    if mean is not None:
                        recovery[key + "_avg"] = round(mean, 0)

            # Activity
            today_str = date.today().isoformat()
            steps = ah.query_records("HKQuantityTypeIdentifierStepCount", today_str, today_str + " 23:59:59")
            if steps:
                from activity_stats import daily_activity_totals
                activity = daily_activity_totals(steps).get(today_str, {})
                recovery["steps"] = round(activity["value"]) if activity.get("value") is not None else None
                recovery["steps_overlap"] = activity.get("overlap", False)
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
