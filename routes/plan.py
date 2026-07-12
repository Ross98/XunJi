"""训练规划路由"""
from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app_state import get_jinja_env, get_cache
from analysis import summarize_training
from planner import generate_plan
from movements_cn import apply_movement_cn_to_trains

router = APIRouter(tags=["plan"])


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

    plan = generate_plan(recent_trains)
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

            # Activity
            today_str = date.today().isoformat()
            steps = ah.query_records("HKQuantityTypeIdentifierStepCount", today_str, today_str + " 23:59:59")
            if steps:
                recovery["steps"] = round(sum(s["value"] for s in steps if s.get("value")))
    except Exception:
        pass

    env = get_jinja_env()
    tmpl = env.get_template("plan.html")
    return HTMLResponse(tmpl.render(
        request=request,
        plan=plan,
        summary=summary,
        recovery=recovery,
        days=30,
    ))
