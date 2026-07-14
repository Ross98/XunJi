from datetime import date, timedelta
import json, os

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app_state import get_jinja_env, get_data_service
from analysis import summarize_diet_detailed, get_latest_weight

router = APIRouter(tags=["diet"])


class UpsertFoodItem(BaseModel):
    date: str
    meal_type: str
    name: str
    amount: float
    unit: str = "g"
    ntr: dict = {}
    uniquekey: str = ""


class UpsertRequest(BaseModel):
    foods: list[UpsertFoodItem]


@router.get("/diet", response_class=HTMLResponse)
async def diet_today(request: Request):
    ds = get_data_service()
    today = date.today().isoformat()

    # Today's diet records
    diet_data = await ds.get_diet(today, today, detail=True)
    records = diet_data.get("res", {}).get("records", [])
    if not records:
        records = diet_data.get("records", [])

    # Latest body weight (last 30 days)
    body_data = await ds.get_body(
        (date.today() - timedelta(days=30)).isoformat(),
        today,
        types=["weight"],
        limit=50,
    )
    body_records = body_data.get("res", {}).get("records", [])
    if not body_records:
        body_records = body_data.get("records", [])
    body_weight = get_latest_weight(body_records)

    env = get_jinja_env()
    tmpl = env.get_template("diet.html")
    return HTMLResponse(tmpl.render(
        request=request,
        records=records,
        body_weight=body_weight,
        today_date=today,
    ))


@router.get("/diet/history", response_class=HTMLResponse)
async def diet_history(
    request: Request,
    range_days: int = Query(90, ge=1, le=365),
):
    ds = get_data_service()
    today = date.today()
    start = (today - timedelta(days=range_days - 1)).isoformat()
    end = today.isoformat()
    diet_data = await ds.get_diet(start, end, detail=True)
    records = diet_data.get("res", {}).get("records", [])
    if not records:
        records = diet_data.get("records", [])
    analysis = summarize_diet_detailed(records)
    dates_with_data = sorted(analysis["daily_calories"].keys())
    env = get_jinja_env()
    tmpl = env.get_template("diet_history.html")
    return HTMLResponse(tmpl.render(
        request=request,
        records=records,
        analysis=analysis,
        dates_with_data=dates_with_data,
        range_days=range_days,
    ))

DIET_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "diet_config.json")


def _load_diet_config() -> dict:
    if os.path.exists(DIET_CONFIG_PATH):
        with open(DIET_CONFIG_PATH) as f:
            return json.load(f)
    return {}


def _save_diet_config(config: dict):
    with open(DIET_CONFIG_PATH, "w") as f:
        json.dump(config, f)


@router.get("/diet/config")
async def get_diet_config():
    return JSONResponse(_load_diet_config())


@router.post("/diet/config")
async def save_diet_config(data: dict):
    config = _load_diet_config()
    config.update(data)
    _save_diet_config(config)
    return {"status": "ok"}




@router.get("/diet/search")
async def diet_search(
    keyword: str = Query(..., min_length=1),
    limit: int = Query(8, ge=1, le=20),
):
    ds = get_data_service()
    data = await ds.search_food(keyword, limit)
    foods = data.get("res", {}).get("foods", [])
    if not foods:
        foods = data.get("foods", [])
    return JSONResponse({"foods": foods})


@router.post("/diet/upsert")
async def diet_upsert(req: UpsertRequest):
    ds = get_data_service()
    td = date.today().isoformat()
    payload = {
        "client_request_id": f"web-{td}",
        "dry_run": False,
        "foods": [f.model_dump() for f in req.foods],
    }
    result = await ds.upsert_diet(payload)
    ds._cache.delete_prefix("diet:")
    return JSONResponse(result)
