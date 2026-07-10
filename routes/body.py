from datetime import date, timedelta
from typing import Optional

import json, os

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from app_state import get_jinja_env, get_data_service
from analysis import summarize_body, body_latest, body_stats, body_changes, calculate_bmi

router = APIRouter(tags=["body"])

BODY_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "body_config.json")


def _load_body_config() -> dict:
    if os.path.exists(BODY_CONFIG_PATH):
        with open(BODY_CONFIG_PATH) as f:
            return json.load(f)
    return {"height_cm": 170}


def _save_body_config(config: dict):
    with open(BODY_CONFIG_PATH, "w") as f:
        json.dump(config, f)

RANGE_MAP = {
    "30": 30,
    "90": 90,
    "180": 180,
    "all": 3650,
}


@router.get("/body", response_class=HTMLResponse)
async def body_page(
    request: Request,
    range: str = Query("90", description="Days range: 30, 90, 180, all"),
):
    ds = get_data_service()
    today = date.today().isoformat()
    days = RANGE_MAP.get(range, 90)
    start = (date.today() - timedelta(days=days)).isoformat()

    body_data = await ds.get_body(start, today)
    records = body_data.get("res", {}).get("records", [])
    if not records:
        records = body_data.get("records", [])

    summary = summarize_body(records)
    latest = body_latest(records)
    stats = body_stats(records)
    changes = body_changes(records)

    config = _load_body_config()
    height_cm = config.get("height_cm", 170)
    latest_weight = latest.get("weight", {}).get("value")
    bmi = calculate_bmi(latest_weight, height_cm)

    # Latest change for stat cards
    weight_recs = [r for r in records if r.get("type") == "weight"]
    bf_recs = [r for r in records if r.get("type") == "bodyfat"]
    latest_weight_change = None
    latest_bf_change = None
    if weight_recs and len(weight_recs) > 1:
        key = f"weight:{weight_recs[0].get('datestr','')}"
        latest_weight_change = changes.get(key)
    if bf_recs and len(bf_recs) > 1:
        key = f"bodyfat:{bf_recs[0].get('datestr','')}"
        latest_bf_change = changes.get(key)

    # Period change (first vs last in range)
    w_chrono = sorted(
        [r for r in records if r.get("type") == "weight" and r.get("value") is not None],
        key=lambda x: x.get("datestr", ""),
    )
    period_weight_change = None
    period_bf_change = None
    if len(w_chrono) >= 2:
        try:
            period_weight_change = round(
                float(w_chrono[-1]["value"]) - float(w_chrono[0]["value"]), 2
            )
        except (ValueError, TypeError):
            pass
    bf_chrono = sorted(
        [r for r in records if r.get("type") == "bodyfat" and r.get("value") is not None],
        key=lambda x: x.get("datestr", ""),
    )
    if len(bf_chrono) >= 2:
        try:
            period_bf_change = round(
                float(bf_chrono[-1]["value"]) - float(bf_chrono[0]["value"]), 2
            )
        except (ValueError, TypeError):
            pass

    env = get_jinja_env()
    tmpl = env.get_template("body.html")
    return HTMLResponse(tmpl.render(
        request=request,
        weight_trend=summary["weight_trend"],
        bodyfat_trend=summary["bodyfat_trend"],
        latest=latest,
        stats=stats,
        changes=changes,
        bmi=bmi,
        range_days=days,
        range_selected=range,
        latest_weight_change=latest_weight_change,
        latest_bf_change=latest_bf_change,
        period_weight_change=period_weight_change,
        period_bf_change=period_bf_change,
        height_cm=height_cm,
        records_data=_build_table_data(records, changes),
    ))


@router.post("/body/settings")
async def update_body_settings(data: dict):
    config = _load_body_config()
    if "height_cm" in data:
        h = data["height_cm"]
        if isinstance(h, (int, float)) and 100 <= h <= 250:
            config["height_cm"] = int(h)
    _save_body_config(config)
    return {"status": "ok"}



def _build_table_data(records: list[dict], changes: dict[str, float]) -> list[dict]:
    """Merge weight and bodyfat records by date into table rows (newest first)."""
    by_date: dict[str, dict] = {}
    for rec in records:
        d = rec.get("datestr", "")
        if not d:
            continue
        if d not in by_date:
            by_date[d] = {"date": d}
        t = rec.get("type", "")
        v = rec.get("value")
        if t and v is not None:
            try:
                by_date[d][t] = round(float(v), 2)
            except (ValueError, TypeError):
                pass
            by_date[d][f"{t}_change"] = changes.get(f"{t}:{d}")
            if t == "weight":
                bmi_val = calculate_bmi(float(v))
                if bmi_val is not None:
                    by_date[d]["bmi"] = bmi_val
    rows = sorted(by_date.values(), key=lambda x: x["date"], reverse=True)
    return rows
