"""Apple Health 数据路由"""

from datetime import date, timedelta
from collections import OrderedDict
from typing import Optional

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from app_state import get_jinja_env
import apple_health as ah

router = APIRouter(tags=["health"])

# 健康中英映射
HEALTH_LABELS = {
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "HRV (ms)",
    "HKQuantityTypeIdentifierRestingHeartRate": "静息心率 (bpm)",
    "HKQuantityTypeIdentifierVO2Max": "VO₂ Max (ml/kg/min)",
    "HKQuantityTypeIdentifierBodyMass": "体重 (kg)",
    "HKQuantityTypeIdentifierBodyFatPercentage": "体脂率 (%)",
}


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
    body_mass = ah.query_records_desc("HKQuantityTypeIdentifierBodyMass", start, end, limit=30)
    body_fat = ah.query_records_desc("HKQuantityTypeIdentifierBodyFatPercentage", start, end, limit=30)
    workouts = ah.query_workouts(start=start, end=end, limit=5)

    body_rows = _build_body_table(body_mass, body_fat, max_rows=5)
    total_body_dates = len(set(r["start_date"][:10] for r in body_mass) | set(r["start_date"][:10] for r in body_fat))

    sleep_summary = ah.get_sleep_summary(start, end)
    sleep_count = len(ah.query_sleep(start, end)) if imported else 0

    return 
    freshness_svc = DataFreshnessService()
    freshness_ctx = freshness_svc.get_freshness_context()
HTMLResponse(tmpl.render(
        request=request,
        freshness=freshness_ctx,
        imported=imported,
        stats=stats,
        hrv=hrv,
        resting_hr=resting_hr,
        vo2=vo2,
        body_rows=body_rows,
        body_row_count=total_body_dates,
        workouts=workouts,
        sleep_summary=sleep_summary,
        sleep_count=sleep_count,
        today=date.today(),
    ))


@router.post("/health/import")
async def trigger_import():
    try:
        total = ah.import_all()
        return {"status": "ok", "total": total}
    except FileNotFoundError as e:
        return {"status": "error", "message": str(e)}


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
