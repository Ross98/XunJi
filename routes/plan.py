from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app_state import get_jinja_env, get_data_service
from analysis import summarize_training
from planner import generate_plan
from movements_cn import apply_movement_cn_to_trains

router = APIRouter(tags=["plan"])


@router.get("/plan", response_class=HTMLResponse)
@router.get("/plan/{days}", response_class=HTMLResponse)
async def plan_page(request: Request, days: int = 14):
    ds = get_data_service()
    today = date.today().isoformat()

    # Fetch recent training data
    recent_trains = []
    d = date.today() - timedelta(days=days)
    while d <= date.today():
        ds_str = d.isoformat()
        data = await ds.get_training(ds_str)
        trains = data.get("res", {}).get("trains", [])
        recent_trains.extend(trains)
        d += timedelta(days=1)

    # Translate movement names to Chinese
    recent_trains = apply_movement_cn_to_trains(recent_trains)

    plan = generate_plan(recent_trains)
    summary = summarize_training(recent_trains)

    env = get_jinja_env()
    tmpl = env.get_template("plan.html")
    return HTMLResponse(tmpl.render(
        request=request,
        plan=plan,
        summary=summary,
        days=days,
    ))
