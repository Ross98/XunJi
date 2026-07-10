"""Apple Health 数据路由"""

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from app_state import get_jinja_env
import apple_health as ah

router = APIRouter(tags=["health"])


@router.get("/health", response_class=HTMLResponse)
async def health_page(request: Request):
    """健康数据总览页面"""
    env = get_jinja_env()
    tmpl = env.get_template("health.html")
    stats = ah.get_import_count()
    imported = stats["total_records"] > 0 or stats["total_workouts"] > 0

    # 最近 90 天的指标
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=90)).isoformat()

    # Recovery 指标
    hrv = ah.query_records(
        "HKQuantityTypeIdentifierHeartRateVariabilitySDNN", start, end, limit=30
    )
    resting_hr = ah.query_records(
        "HKQuantityTypeIdentifierRestingHeartRate", start, end, limit=30
    )
    vo2 = ah.query_records(
        "HKQuantityTypeIdentifierVO2Max", start, end, limit=30
    )
    body_mass = ah.query_records(
        "HKQuantityTypeIdentifierBodyMass", start, end
    )
    body_fat = ah.query_records(
        "HKQuantityTypeIdentifierBodyFatPercentage", start, end
    )

    # Workout 汇总
    workouts = ah.query_workouts(start=start, end=end, limit=50)

    return HTMLResponse(tmpl.render(
        request=request,
        imported=imported,
        stats=stats,
        hrv=[r for r in hrv[-30:]],
        resting_hr=[r for r in resting_hr[-30:]],
        vo2=[r for r in vo2[-30:]],
        body_mass=body_mass,
        body_fat=body_fat,
        workouts=workouts,
        today=date.today(),
    ))


@router.post("/health/import")
async def trigger_import():
    """触发 Apple Health 数据导入"""
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
    """查询特定健康指标数据 (JSON API)"""
    records = ah.query_records(metric, start, end, limit)
    return {"data": records}


@router.get("/health/workouts")
async def health_workouts(
    activity: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    limit: int = Query(50),
):
    """查询 Apple Watch 训练记录 (JSON API)"""
    recs = ah.query_workouts(activity, start, end, limit)
    return {"data": recs}
