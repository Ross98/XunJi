"""仪表盘路由"""

from datetime import date, timedelta
from collections import defaultdict
import json
from typing import Optional
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app_state import get_jinja_env, get_data_service
from data_service import DataFreshnessService, get_cache
from analysis import summarize_training, summarize_body, body_latest
import apple_health as _apple_health

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


def _ma7(trend: list[dict]) -> list:
    """7-day moving average from trend data."""
    if len(trend) < 7:
        return []
    vals = [p["value"] for p in trend]
    out = []
    for i in range(len(vals)):
        if i < 6:
            out.append(None)
        else:
            out.append(round(sum(vals[i-6:i+1]) / 7, 1))
    return out


def _chg7(trend: list[dict]) -> Optional[float]:
    """Change over last 7 days (latest - 7d ago)."""
    if len(trend) < 8:
        return None
    return round(trend[-1]["value"] - trend[-8]["value"], 1)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    ds = get_data_service()
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    # ── 训记数据: 缓存优先 + 后台刷新 ──
    training_data = await ds.get_training(yesterday)
    trains = training_data.get("res", {}).get("trains", [])
    summary = summarize_training(trains)

    # 训练类型标签 & set 数
    all_types = set()
    total_sets = 0
    for t in trains:
        for m in t.get("movements", []):
            mt = m.get("type") or ""
            if not mt or mt[0].islower():
                mt = m.get("name", "")
            # 只取英文 workout type 并转为可读标签
            TYPE_ALIAS = {"TraditionalStrengthTraining":"力量训练", "FunctionalStrengthTraining":"功能性力量", "HighIntensityIntervalTraining":"HIIT", "Walking":"步行", "Running":"跑步", "Cycling":"骑行", "Swimming":"游泳", "Hiking":"徒步", "Yoga":"瑜伽", "CoreTraining":"核心训练", "Flexibility":"柔韧性"}
            alias = TYPE_ALIAS.get(mt, "")
            if alias:
                all_types.add(alias)
            for s in m.get("sets", []):
                total_sets += len(s.get("items", s))
    train_type_str = " · ".join(sorted(all_types)[:2]) if all_types else ""

    # body: 先读缓存, 后台异步刷新
    cache = get_cache()
    three_months = (date.today() - timedelta(days=90)).isoformat()
    body_cache_key = f"body:all:{three_months}:{today}:500"
    cached_body = cache.get(body_cache_key)
    if cached_body:
        records = cached_body.get("res", {}).get("records", [])
        if not records:
            records = cached_body.get("records", [])
        # 后台刷新缓存, 失败不影响当前页面
        async def _refresh_body():
            try:
                fresh = await ds.get_body(three_months, today)
                cache.set(body_cache_key, fresh)
            except Exception:
                pass  # API 挂了 → 保持旧缓存
        asyncio.ensure_future(_refresh_body())
    else:
        # 首次无缓存 → 正常等 API
        body_data = await ds.get_body(three_months, today)
        records = body_data.get("res", {}).get("records", [])
        if not records:
            records = body_data.get("records", [])
    body_summary = summarize_body(records)
    latest_body = body_latest(records)

    diet_data = await ds.get_diet(yesterday, yesterday)
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

        step_raw = ah.query_records("HKQuantityTypeIdentifierStepCount", yesterday, yesterday + " 23:59:59")
        today_steps = sum(r["value"] for r in step_raw if r.get("value")) if step_raw else 0
        ex_raw = ah.query_records("HKQuantityTypeIdentifierAppleExerciseTime", yesterday, yesterday + " 23:59:59")
        today_exercise = sum(r["value"] for r in ex_raw if r.get("value")) if ex_raw else 0
        stand_raw = ah.query_records_desc("HKQuantityTypeIdentifierAppleStandTime", yesterday, yesterday + " 23:59:59")
        today_stand = round(sum(r["value"] for r in stand_raw if r.get("value"))) if stand_raw else 0

        # 90d averages for context
        step_90d = ah.query_records("HKQuantityTypeIdentifierStepCount", start, end)
        step_by_day = {}
        for r in step_90d:
            d = r["start_date"][:10]
            v = r.get("value", 0)
            if v: step_by_day[d] = step_by_day.get(d, 0) + v
        step_avg = round(sum(step_by_day.values()) / max(len(step_by_day), 1))

        ex_90d = ah.query_records("HKQuantityTypeIdentifierAppleExerciseTime", start, end)
        ex_by_day = {}
        for r in ex_90d:
            d = r["start_date"][:10]
            v = r.get("value", 0)
            if v: ex_by_day[d] = ex_by_day.get(d, 0) + v
        ex_avg = round(sum(ex_by_day.values()) / max(len(ex_by_day), 1))

        stand_90d = ah.query_records("HKQuantityTypeIdentifierAppleStandTime", start, end)
        stand_by_day = {}
        for r in stand_90d:
            d = r["start_date"][:10]
            v = r.get("value", 0)
            if v: stand_by_day[d] = stand_by_day.get(d, 0) + v
        stand_avg = round(sum(stand_by_day.values()) / max(len(stand_by_day), 1)) if stand_by_day else 0

        # Sleep summary (last night)
        sleep_data = ah.get_sleep_summary(start, end)
        sleep_last = sleep_data[0] if sleep_data else None

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
            "yesterday": yesterday,
            "today_stand": today_stand,
            "step_avg": step_avg,
            "ex_avg": ex_avg,
            "stand_avg": stand_avg,
            "hrv_ma7": json.dumps(_ma7(hrv_trend)),
            "rhr_ma7": json.dumps(_ma7(rhr_trend)),
            "vo2_ma7": json.dumps(_ma7(vo2_trend)),
            "hrv_chg7": _chg7(hrv_trend),
            "rhr_chg7": _chg7(rhr_trend),
            "vo2_chg7": _chg7(vo2_trend),
            "sleep_last": sleep_last,
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

                da_30 = (date.today() - timedelta(days=30)).isoformat()
                hrv_30 = [r for r in hrv_raw if r["start_date"][:10] >= da_30[:10]]
                hrv_chart = _build_trend(hrv_30)

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

    # Force Apple Health data (override training count & calories)
    try:
        aw_wos = _apple_health.query_workouts(start=yesterday, end=yesterday + " 23:59:59")
        if aw_wos:
            aw_c = len(aw_wos)
            if aw_c > summary.get("total_days", 0):
                summary["total_days"] = aw_c
    except Exception:
        pass
    try:
        cal_recs = _apple_health.query_records("HKQuantityTypeIdentifierActiveEnergyBurned", yesterday, yesterday + " 23:59:59")
        if cal_recs:
            cal_sum = round(sum(r["value"] for r in cal_recs if r.get("value")))
            if cal_sum > 0:
                today_calories = cal_sum
    except Exception:
        pass

    env = get_jinja_env()
    tmpl = env.get_template("dashboard.html")

    # 体重周变化 (取最近两次 weigh-in)
    weight_chg7 = None
    wt = body_summary.get("weight_trend", [])
    if len(wt) >= 2:
        w1 = wt[-1].get("value")
        w0 = wt[-2].get("value")
        if w1 is not None and w0 is not None:
            weight_chg7 = round(float(w1) - float(w0), 1)

    return 
    freshness_svc = DataFreshnessService()
    freshness_ctx = freshness_svc.get_freshness_context()
HTMLResponse(tmpl.render(
        request=request,
        freshness=freshness_ctx,
        summary=summary,
        weight_trend=body_summary["weight_trend"],
        latest_weight=latest_body.get("weight", {}).get("value", "--"),
        today_calories=today_calories,
        health=health_data,
        recovery=recovery,
        total_sets=total_sets,
        train_type_str=train_type_str,
        weight_chg7=weight_chg7,
    ))