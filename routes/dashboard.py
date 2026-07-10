from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app_state import get_jinja_env, get_data_service
from analysis import summarize_training, summarize_body, body_latest

router = APIRouter(tags=["dashboard"])


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    ds = get_data_service()
    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    three_months = (date.today() - timedelta(days=90)).isoformat()

    # Fetch recent training
    training_data = await ds.get_training(today)
    trains = training_data.get("res", {}).get("trains", [])
    summary = summarize_training(trains)

    # Fetch body data for weight trend
    body_data = await ds.get_body(three_months, today)
    records = body_data.get("res", {}).get("records", [])
    if not records:
        records = body_data.get("records", [])
    body_summary = summarize_body(records)
    latest_body = body_latest(records)

    # Fetch today's diet
    diet_data = await ds.get_diet(today, today)
    diet_records = diet_data.get("res", {}).get("records", [])
    if not diet_records:
        diet_records = diet_data.get("records", [])
    today_calories = sum(
        r.get("ntr", {}).get("cal", 0) for r in diet_records if r.get("date") == today
    )

    env = get_jinja_env()
    tmpl = env.get_template("dashboard.html")
    return HTMLResponse(tmpl.render(
        request=request,
        summary=summary,
        weight_trend=body_summary["weight_trend"],
        latest_weight=latest_body.get("weight", {}).get("value", "--"),
        today_calories=today_calories,
    ))
