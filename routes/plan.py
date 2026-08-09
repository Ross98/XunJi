"""训练规划路由"""
from datetime import date, timedelta
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app_state import get_jinja_env, get_cache
from analysis import summarize_training
from planner import DEFAULT_PLAN_PREFERENCES, generate_plan
from movements_cn import apply_movement_cn_to_trains

router = APIRouter(tags=["plan"])
logger = logging.getLogger(__name__)
PLAN_CONFIG_PATH = Path(__file__).resolve().parent.parent / "plan_config.json"


def _load_plan_preferences() -> dict:
    preferences = dict(DEFAULT_PLAN_PREFERENCES)
    if PLAN_CONFIG_PATH.exists():
        try:
            saved = json.loads(PLAN_CONFIG_PATH.read_text(encoding="utf-8"))
            preferences.update({key: value for key, value in saved.items() if key in preferences})
        except (OSError, ValueError, TypeError):
            logger.exception("Unable to load plan preferences")
    return preferences


def _save_plan_preferences(preferences: dict):
    PLAN_CONFIG_PATH.write_text(
        json.dumps(preferences, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@router.get("/plan", response_class=HTMLResponse)
async def plan_page(request: Request):
    today = date.today().isoformat()

    # Fetch last 30 days of training from cache
    cache = get_cache()
    recent_trains = []
    for i in range(30):
        d = (date.today() - timedelta(days=i)).isoformat()
        cached = cache.get(f"training:{d}:False") or cache.get(f"training:{d}:True")
        if cached:
            trains = cached.get("res", {}).get("trains", []) or cached.get("trains", [])
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
            sleep_data = ah.get_sleep_summary(start, today)
            if sleep_data:
                sl = sleep_data[0]
                st = sl.get("AsleepUnspecified", 0) + sl.get("AsleepCore", 0) + sl.get("AsleepREM", 0) + sl.get("AsleepDeep", 0)
                recovery["sleep_hours"] = round(st / 60, 1)
                recovery["sleep_deep_pct"] = round(sl.get("AsleepDeep", 0) / st * 100, 0) if st else 0
                recovery["sleep_rem_pct"] = round(sl.get("AsleepREM", 0) / st * 100, 0) if st else 0
                recovery["sleep_core_pct"] = round(sl.get("AsleepCore", 0) / st * 100, 0) if st else 0
                recovery["sleep_awake"] = sl.get("Awake", 0)

            # HRV
            hrv = ah.query_records("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", start, today)
            if hrv:
                recovery["hrv"] = round(hrv[-1]["value"], 0)
                # 30d avg
                vals = [r["value"] for r in hrv if r.get("value")]
                if vals:
                    recovery["hrv_avg"] = round(sum(vals) / len(vals), 0)

            # Resting HR
            rhr = ah.query_records("HKQuantityTypeIdentifierRestingHeartRate", start, today)
            if rhr:
                recovery["rhr"] = round(rhr[-1]["value"], 0)
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
        preferences["days_per_week"] = min(7, max(1, int(preferences["days_per_week"])))
        preferences["session_minutes"] = min(180, max(15, int(preferences["session_minutes"])))
    except (TypeError, ValueError):
        return {"status": "error", "message": "训练天数或时长格式不正确"}
    _save_plan_preferences(preferences)
    return {"status": "ok", "preferences": preferences}
