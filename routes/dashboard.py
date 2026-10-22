"""仪表盘路由"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from collections import defaultdict
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse
from app_state import get_cache, get_jinja_env, get_data_service
from data_service import (
    DataFreshnessService,
    DataService,
    get_cached_training,
    extract_trains as _extract_trains,
    extract_body_records as _extract_body_records,
)
from analysis import summarize_training, summarize_body, body_latest
from sleep_stats import confirmed_sleep_minutes

router = APIRouter(tags=["dashboard"])
logger = logging.getLogger(__name__)
_BODY_REFRESH_TTL = timedelta(minutes=15)
_TRAINING_TYPE_ALIASES = {
    "TraditionalStrengthTraining": "力量训练",
    "FunctionalStrengthTraining": "功能性力量",
    "HighIntensityIntervalTraining": "HIIT",
    "Walking": "步行",
    "Running": "跑步",
    "Cycling": "骑行",
    "Swimming": "游泳",
    "Hiking": "徒步",
    "Yoga": "瑜伽",
    "CoreTraining": "核心训练",
    "Flexibility": "柔韧性",
}

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
        result.append({"date": d[5:], "iso_date": d, "value": round(sum(vals) / len(vals), 1)})
    return result


def _ma7(trend: list[dict]) -> list:
    """7-day moving average from trend data."""
    if len(trend) < 7:
        return []
    out = []
    by_date = {date.fromisoformat(p["iso_date"]): p["value"] for p in trend}
    for point in trend:
        end = date.fromisoformat(point["iso_date"])
        values = [by_date.get(end - timedelta(days=offset)) for offset in range(7)]
        out.append(round(sum(values) / 7, 1) if all(v is not None for v in values) else None)
    return out


def _chg7(trend: list[dict]) -> Optional[float]:
    """Change over last 7 days (latest - 7d ago)."""
    if not trend:
        return None
    last = trend[-1]
    previous_day = (date.fromisoformat(last["iso_date"]) - timedelta(days=7)).isoformat()
    previous = next((point for point in trend if point["iso_date"] == previous_day), None)
    return round(last["value"] - previous["value"], 1) if previous else None


async def _get_training_or_empty(ds, training_date: str) -> dict:
    """Keep dashboard available when training API is temporarily unavailable."""
    try:
        return await ds.get_training(training_date)
    except Exception:
        logger.exception("Training API unavailable for %s", training_date)
        return {"res": {"trains": []}}


async def _refresh_body_cache(ds, start: str, end: str) -> None:
    try:
        await ds.get_body(start, end, force_refresh=True)
    except Exception:
        logger.exception("Body API background refresh failed")


def _body_cache_needs_refresh(
    cache, cache_key: str, now: datetime | None = None
) -> bool:
    get_mtime = getattr(cache, "get_mtime", None)
    if get_mtime is None:
        return True
    mtime = get_mtime(cache_key)
    if not mtime:
        return True
    try:
        cached_at = datetime.fromisoformat(mtime)
    except ValueError:
        return True
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=timezone.utc)
    current_time = now or datetime.now(timezone.utc)
    return current_time - cached_at > _BODY_REFRESH_TTL


async def _get_body_records(
    ds,
    cache,
    cache_key: str,
    start: str,
    end: str,
    background_tasks: BackgroundTasks | None = None,
) -> list[dict]:
    cached = cache.get(cache_key)
    if DataService.body_history_complete(cached):
        if background_tasks is not None and _body_cache_needs_refresh(cache, cache_key):
            background_tasks.add_task(_refresh_body_cache, ds, start, end)
        return _extract_body_records(cached)
    try:
        return _extract_body_records(await ds.get_body(start, end))
    except Exception:
        logger.exception("Body API unavailable and no cache exists")
        return []


def _get_cached_training_dates(cache, end_date: str, days: int = 14) -> set[str]:
    end = date.fromisoformat(end_date)
    training_dates: set[str] = set()
    for offset in range(days):
        current = (end - timedelta(days=offset)).isoformat()
        cached = get_cached_training(cache, current)
        for train in _extract_trains(cached):
            training_date = train.get("datestr")
            if training_date:
                training_dates.add(training_date)
    return training_dates


def _get_daily_health_total(ah, metric_type: str, preferred_date: str) -> tuple[float, str | None]:
    def total_for(metric_date: str) -> float:
        records = ah.query_records(
            metric_type, metric_date, metric_date + " 23:59:59"
        )
        return sum(float(record["value"]) for record in records if record.get("value"))

    preferred_total = total_for(preferred_date)
    if preferred_total > 0:
        return preferred_total, preferred_date

    latest = ah.get_latest_dates(metric_type).get(metric_type)
    if latest and latest != preferred_date:
        return total_for(latest), latest
    return 0, latest or preferred_date


@router.get("/api/ah-check")
async def ah_import_check():
    """Check Apple Health import freshness."""
    try:
        import apple_health as _ah
        counts = _ah.get_import_count()
        t = counts.get("health_import_time")
        return {"import_time": t, "ok": True}
    except Exception as e:
        return {"import_time": None, "ok": False, "error": str(e)}


@router.get("/", response_class=HTMLResponse)
async def dashboard_loader(request: Request):
    """Return immediately while the browser loads the expensive dashboard."""
    env = get_jinja_env()
    tmpl = env.get_template("dashboard_loader.html")
    return HTMLResponse(tmpl.render(request=request))


@router.get("/dashboard/content", response_class=HTMLResponse)
async def dashboard_content(request: Request, background_tasks: BackgroundTasks):
    ds = get_data_service()
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    # ── 训记数据: 缓存优先 + 后台刷新 ──
    training_data = await _get_training_or_empty(ds, yesterday)
    trains = _extract_trains(training_data)
    summary = summarize_training(trains)
    training_sessions = len(trains)
    training_minutes = 0
    for train in trains:
        cardio = train.get("_cardio_metrics", {})
        try:
            training_minutes += float(cardio.get("total_duration_min") or 0)
        except (TypeError, ValueError):
            pass

    # 训练类型标签 & set 数
    all_types = set()
    total_sets = summary["total_sets"]
    for t in trains:
        for m in t.get("movements", []):
            mt = m.get("type") or ""
            if not mt or mt[0].islower():
                mt = m.get("name", "")
            alias = _TRAINING_TYPE_ALIASES.get(mt, "")
            if alias:
                all_types.add(alias)
    train_type_str = " · ".join(sorted(all_types)[:2]) if all_types else ""

    # body: 先读缓存, 后台异步刷新
    cache = get_cache()
    three_months = (date.today() - timedelta(days=90)).isoformat()
    body_cache_key = f"body:all:{three_months}:{today}:500"
    records = await _get_body_records(
        ds, cache, body_cache_key, three_months, today, background_tasks
    )
    body_summary = summarize_body(records)
    latest_body = body_latest(records)

    yesterday_active_calories = None

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

        # Prefer yesterday, then report the actual fallback date when data is stale.
        today_steps, step_date = _get_daily_health_total(
            ah, "HKQuantityTypeIdentifierStepCount", yesterday
        )
        today_exercise, exercise_date = _get_daily_health_total(
            ah, "HKQuantityTypeIdentifierAppleExerciseTime", yesterday
        )
        today_stand, stand_date = _get_daily_health_total(
            ah, "HKQuantityTypeIdentifierAppleStandTime", yesterday
        )

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
        sleep_total_min = confirmed_sleep_minutes(sleep_last)
        sleep_deep_pct = None
        if sleep_total_min is not None:
            sleep_deep_pct = round((sleep_last.get("AsleepDeep", 0) or 0) / sleep_total_min * 100)

        latest_hrv = hrv_raw[-1]["value"] if hrv_raw else None
        latest_rhr = rhr_raw[-1]["value"] if rhr_raw else None
        latest_vo2 = vo2_raw[-1]["value"] if vo2_raw else None

        health_data = {
            "has_data": True,
            "hrv_trend": hrv_trend,
            "rhr_trend": rhr_trend,
            "vo2_trend": vo2_trend,
            "latest_hrv": latest_hrv,
            "latest_rhr": latest_rhr,
            "latest_vo2": latest_vo2,
            "hrv_date": hrv_raw[-1]["start_date"][:10] if hrv_raw else None,
            "rhr_date": rhr_raw[-1]["start_date"][:10] if rhr_raw else None,
            "vo2_date": vo2_raw[-1]["start_date"][:10] if vo2_raw else None,
            "today_steps": int(today_steps),
            "today_exercise": int(today_exercise),
            "step_date": step_date,
            "exercise_date": exercise_date,
            "stand_date": stand_date,
            "yesterday": yesterday,
            "today_stand": round(today_stand),
            "step_avg": step_avg,
            "ex_avg": ex_avg,
            "stand_avg": stand_avg,
            "hrv_ma7": _ma7(hrv_trend),
            "rhr_ma7": _ma7(rhr_trend),
            "vo2_ma7": _ma7(vo2_trend),
            "hrv_chg7": _chg7(hrv_trend),
            "rhr_chg7": _chg7(rhr_trend),
            "vo2_chg7": _chg7(vo2_trend),
            "sleep_last": sleep_last,
            "sleep_total_hours": round(sleep_total_min / 60, 1) if sleep_total_min else None,
            "sleep_deep_pct": sleep_deep_pct,
        }

        # ── 恢复分析 (训练日 vs 休息日) ──
        try:
            # 恢复分析只读缓存，避免冷启动时并发请求 Open API。
            training_dates = _get_cached_training_dates(cache, today, days=14)

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
                    "chart_data": hrv_chart,
                    "train_avg": train_avg,
                    "rest_avg": rest_avg,
                    "today_hrv": today_val,
                    "vs_train_pct": vs_train,
                    "train_days": len(training_dates),
                    "train_samples": len(train_vals),
                    "rest_samples": len(rest_vals),
                }
        except Exception:
            logger.exception("Recovery analysis failed")
            recovery = None

        # Apple Health is the only source for activity energy expenditure.
        try:
            aw_wos = ah.query_workouts(
                start=yesterday, end=yesterday + " 23:59:59"
            )
            if len(aw_wos) > summary.get("total_days", 0):
                summary["total_days"] = len(aw_wos)
            cal_recs = ah.query_records(
                "HKQuantityTypeIdentifierActiveEnergyBurned",
                yesterday,
                yesterday + " 23:59:59",
            )
            cal_sum = round(sum(r["value"] for r in cal_recs if r.get("value")))
            if cal_sum > 0:
                yesterday_active_calories = cal_sum
        except Exception:
            logger.exception("Apple Health workout summary failed")

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

    latest_weight_date = latest_body.get("weight", {}).get("date", "")
    latest_weight_source = "训记"
    for record in records:
        if record.get("type") == "weight" and record.get("datestr") == latest_weight_date:
            if record.get("source") == "apple_health":
                latest_weight_source = "Apple Health"
            break

    freshness_svc = DataFreshnessService(xunji_cache=cache)
    freshness_ctx = freshness_svc.get_freshness_context()
    context = {
        "request": request,
        "freshness": freshness_ctx,
        "summary": summary,
        "weight_trend": body_summary["weight_trend"],
        "latest_weight": latest_body.get("weight", {}).get("value", "--"),
        "yesterday_active_calories": yesterday_active_calories,
        "health": health_data,
        "recovery": recovery,
        "total_sets": total_sets,
        "train_type_str": train_type_str,
        "weight_chg7": weight_chg7,
        "latest_weight_date": latest_weight_date,
        "latest_weight_source": latest_weight_source,
        "training_sessions": training_sessions,
        "training_minutes": round(training_minutes),
    }
    template_context = tmpl.new_context(context)
    content_html = "".join(tmpl.blocks["content"](template_context))
    return HTMLResponse(content_html)
