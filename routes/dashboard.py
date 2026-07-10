"""仪表盘路由"""

from datetime import date, timedelta
from collections import defaultdict

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app_state import get_jinja_env, get_data_service
from analysis import summarize_training, summarize_body, body_latest
import json

router = APIRouter(tags=["dashboard"])

HEALTH_DB = None
_ah = None


def _get_ah():
    global _ah
    if _ah is None:
        import importlib, apple_health
        _ah = apple_health
    return _ah


def _build_trend(records: list[dict], label: str, unit: str) -> list[dict]:
    """Aggregate Apple Health records by date (average per day), return Chart.js compatible."""
    by_date: dict[str, list[float]] = defaultdict(list)
    for r in records:
        d = r.get("start_date", "")[:10]
        v = r.get("value")
        if d and v is not None:
            by_date[d].append(float(v))
    result = []
    for d in sorted(by_date.keys()):
        vals = by_date[d]
        avg = sum(vals) / len(vals)
        result.append({"date": d[5:], "value": round(avg, 1), "label": label, "unit": unit})
    return result


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    ds = get_data_service()
    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    three_months = (date.today() - timedelta(days=90)).isoformat()

    training_data = await ds.get_training(today)
    trains = training_data.get("res", {}).get("trains", [])
    summary = summarize_training(trains)

    body_data = await ds.get_body(three_months, today)
    records = body_data.get("res", {}).get("records", [])
    if not records:
        records = body_data.get("records", [])
    body_summary = summarize_body(records)
    latest_body = body_latest(records)

    diet_data = await ds.get_diet(today, today)
    diet_records = diet_data.get("res", {}).get("records", [])
    if not diet_records:
        diet_records = diet_data.get("records", [])
    today_calories = sum(
        r.get("ntr", {}).get("cal", 0) for r in diet_records if r.get("date") == today
    )

    # Apple Health data
    try:
        ah = _get_ah()
        has_health = ah.HEALTH_DB_PATH.exists()
    except Exception:
        has_health = False

    health_data = None
    if has_health:
        ah = _get_ah()
        today_str = date.today().isoformat()
        end = today_str
        start = (date.today() - timedelta(days=90)).isoformat()

        hrv_raw = ah.query_records("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", start, end)
        rhr_raw = ah.query_records("HKQuantityTypeIdentifierRestingHeartRate", start, end)
        vo2_raw = ah.query_records("HKQuantityTypeIdentifierVO2Max", start, end)

        hrv_trend = _build_trend(hrv_raw, "HRV", "ms")
        rhr_trend = _build_trend(rhr_raw, "静息心率", "bpm")
        vo2_trend = _build_trend(vo2_raw, "VO₂ Max", "ml/kg/min")

        # Today step count
        step_raw = ah.query_records("HKQuantityTypeIdentifierStepCount", today_str, today_str)
        today_steps = sum(r["value"] for r in step_raw if r.get("value")) if step_raw else 0

        # Today exercise min
        ex_raw = ah.query_records("HKQuantityTypeIdentifierAppleExerciseTime", today_str, today_str)
        today_exercise = sum(r["value"] for r in ex_raw if r.get("value")) if ex_raw else 0

        # Today stand hours
        stand_raw = ah.query_records_desc("HKCategoryTypeIdentifierAppleStandHour", today_str, today_str)
        today_stand = sum(1 for r in stand_raw if r.get("value") and r["value"] >= 1) if stand_raw else 0

        # Latest values for cards
        latest_hrv = hrv_raw[-1]["value"] if hrv_raw else None
        latest_rhr = rhr_raw[-1]["value"] if rhr_raw else None
        latest_vo2 = vo2_raw[-1]["value"] if vo2_raw else None
        hrv_date = hrv_raw[-1]["start_date"][:10] if hrv_raw else ""
        rhr_date = rhr_raw[-1]["start_date"][:10] if rhr_raw else ""
        vo2_date = vo2_raw[-1]["start_date"][:10] if vo2_raw else ""

        health_data = {
            "has_data": True,
            "hrv_trend": json.dumps(hrv_trend),
            "rhr_trend": json.dumps(rhr_trend),
            "vo2_trend": json.dumps(vo2_trend),
            "latest_hrv": latest_hrv,
            "latest_rhr": latest_rhr,
            "latest_vo2": latest_vo2,
            "hrv_date": hrv_date,
            "rhr_date": rhr_date,
            "vo2_date": vo2_date,
            "today_steps": int(today_steps),
            "today_exercise": int(today_exercise),
            "today_stand": today_stand,
        }

    env = get_jinja_env()
    tmpl = env.get_template("dashboard.html")
    return HTMLResponse(tmpl.render(
        request=request,
        summary=summary,
        weight_trend=body_summary["weight_trend"],
        latest_weight=latest_body.get("weight", {}).get("value", "--"),
        today_calories=today_calories,
        health=health_data,
    ))
