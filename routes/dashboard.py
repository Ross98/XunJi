"""仪表盘路由"""

from datetime import date, timedelta
from collections import defaultdict
import json
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app_state import get_jinja_env, get_data_service
from analysis import summarize_training, summarize_body, body_latest

router = APIRouter(tags=["dashboard"])

_ah = None
def _get_ah():
    global _ah
    if _ah is None:
        import apple_health
        _ah = apple_health
    return _ah


def _build_trend(records: list[dict]) -> list[dict]:
    """Aggregate Apple Health records by date (avg per day)."""
    by_date = defaultdict(list)
    for r in records:
        d = r.get("start_date", "")[:10]
        v = r.get("value")
        if d and v is not None:
            by_date[d].append(float(v))
    result = []
    for d in sorted(by_date.keys()):
        vals = by_date[d]
        result.append({"date": d[5:], "value": round(sum(vals) / len(vals), 1)})
    return result


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    ds = get_data_service()
    today = date.today().isoformat()
    three_months = (date.today() - timedelta(days=90)).isoformat()

    # ── 训记数据 ──
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

    # ── Apple Health 数据 ──
    health_data = None
    recovery = None
    try:
        ah = _get_ah()
        has_health = ah.HEALTH_DB_PATH.exists()
    except Exception:
        has_health = False

    if has_health:
        ah = _get_ah()
        end = today + ' 23:59:59'
        start = (date.today() - timedelta(days=90)).isoformat() + ' 00:00:00'

        hrv_raw = ah.query_records("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", start, end)
        rhr_raw = ah.query_records("HKQuantityTypeIdentifierRestingHeartRate", start, end)
        vo2_raw = ah.query_records("HKQuantityTypeIdentifierVO2Max", start, end)

        hrv_trend = _build_trend(hrv_raw)
        rhr_trend = _build_trend(rhr_raw)
        vo2_trend = _build_trend(vo2_raw)

        step_raw = ah.query_records("HKQuantityTypeIdentifierStepCount", today, today)
        today_steps = sum(r["value"] for r in step_raw if r.get("value")) if step_raw else 0
        ex_raw = ah.query_records("HKQuantityTypeIdentifierAppleExerciseTime", today, today)
        today_exercise = sum(r["value"] for r in ex_raw if r.get("value")) if ex_raw else 0
        stand_raw = ah.query_records_desc("HKCategoryTypeIdentifierAppleStandHour", today, today)
        today_stand = sum(1 for r in stand_raw if r.get("value") and r["value"] >= 1) if stand_raw else 0

        latest_hrv = hrv_raw[-1]["value"] if hrv_raw else None
        latest_rhr = rhr_raw[-1]["value"] if rhr_raw else None
        latest_vo2 = vo2_raw[-1]["value"] if vo2_raw else None

        health_data = {
            "has_data": True,
            "hrv_trend": json.dumps(hrv_trend),
            "rhr_trend": json.dumps(rhr_trend),
            "vo2_trend": json.dumps(vo2_trend),
            "latest_hrv": latest_hrv,
            "latest_rhr": latest_rhr,
            "latest_vo2": latest_vo2,
            "today_steps": int(today_steps),
            "today_exercise": int(today_exercise),
            "today_stand": today_stand,
        }

        # ── 恢复分析 (训练日 vs 休息日) ──
        try:
            # 获取最近 14 天训练数据
            tasks = []
            for i in range(14):
                d = (date.today() - timedelta(days=i)).isoformat()
                tasks.append(ds.get_training(d))
            train_results = await asyncio.gather(*tasks, return_exceptions=True)

            training_dates = set()
            for res in train_results:
                if isinstance(res, dict):
                    trs = res.get("res", {}).get("trains", None) or res.get("trains", [])
                    for t in trs:
                        td = t.get("datestr", "")
                        if td:
                            training_dates.add(td)

            if training_dates and hrv_raw:
                # Normalize training dates to MM-DD
                train_mmdd = {d[5:] for d in training_dates}

                da_14 = (date.today() - timedelta(days=14)).isoformat()
                hrv_14 = [r for r in hrv_raw if r["start_date"][:10] >= da_14[:10]]
                hrv_chart = _build_trend(hrv_14)

                for p in hrv_chart:
                    p["train_day"] = p["date"] in train_mmdd

                # 平均值计算
                train_vals, rest_vals = [], []
                for d in hrv_chart:
                    date_full = "2026-" + d["date"]
                    val = d["value"]
                    if d["train_day"] and val:
                        train_vals.append(val)
                    elif not d["train_day"] and val:
                        rest_vals.append(val)

                train_avg = round(sum(train_vals) / len(train_vals), 1) if train_vals else None
                rest_avg = round(sum(rest_vals) / len(rest_vals), 1) if rest_vals else None
                today_val = hrv_chart[-1]["value"] if hrv_chart else None
                vs_train = round(today_val / train_avg * 100 - 100, 1) if today_val and train_avg else None

                recovery = {
                    "chart_data": json.dumps(hrv_chart),
                    "train_avg": train_avg,
                    "rest_avg": rest_avg,
                    "today_hrv": today_val,
                    "vs_train_pct": vs_train,
                    "train_days": len(training_dates),
                }
        except Exception:
            recovery = None

    env = get_jinja_env()
    tmpl = env.get_template("dashboard.html")
    return HTMLResponse(tmpl.render(
        request=request,
        summary=summary,
        weight_trend=body_summary["weight_trend"],
        latest_weight=latest_body.get("weight", {}).get("value", "--"),
        today_calories=today_calories,
        health=health_data,
        recovery=recovery,
    ))