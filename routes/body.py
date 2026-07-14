from datetime import date, timedelta
from typing import Optional

import json, os

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from app_state import get_jinja_env, get_data_service
from data_service import DataFreshnessService
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
    "all": 3650,
}


def _merge_apple_health(records: list[dict], start: str, end: str) -> tuple[list[dict], set]:
    """将 Apple Health 身体数据合并到训记记录中。
    返回 (合并后的记录, 有 Apple Health 数据的日期集合)。
    """
    import apple_health as ah

    ah_weight = ah.query_records_desc("HKQuantityTypeIdentifierBodyMass", start, end)
    ah_bf = ah.query_records_desc("HKQuantityTypeIdentifierBodyFatPercentage", start, end)

    if not ah_weight and not ah_bf:
        return records, set()

    ah_dates = set()
    # Convert Apple Health records to 训记 format, merge by date
    # Key: date string -> {type: value}
    ah_by_date: dict[str, dict] = {}

    for r in ah_weight:
        d = r["start_date"][:10]
        ah_dates.add(d)
        if d not in ah_by_date:
            ah_by_date[d] = {}
        # Keep the first (most recent) value for each date
        if "weight" not in ah_by_date[d]:
            ah_by_date[d]["weight"] = round(r["value"], 2) if r["value"] else None

    for r in ah_bf:
        d = r["start_date"][:10]
        ah_dates.add(d)
        if d not in ah_by_date:
            ah_by_date[d] = {}
        if "bodyfat" not in ah_by_date[d]:
            # Apple Health stores body fat as decimal 0.19 = 19%
            bf_val = round(r["value"] * 100, 1) if r["value"] else None
            ah_by_date[d]["bodyfat"] = bf_val

    # Build merged records list
    existing_dates = set()
    for rec in records:
        d = rec.get("datestr", "")
        if d:
            existing_dates.add(d)

    merged = list(records)

    for d, vals in ah_by_date.items():
        for typ, val in vals.items():
            if val is None:
                continue
            # Skip if 训记 already has this type on this date
            has_existing = False
            for existing in records:
                if existing.get("datestr") == d and existing.get("type") == typ:
                    has_existing = True
                    break
            if not has_existing:
                merged.append({
                    "datestr": d,
                    "type": typ,
                    "value": str(val),
                    "source": "apple_health",
                })

    return merged, ah_dates


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
    # API 返回大量数据时 records 可能是 JSON 字符串
    if isinstance(records, str):
        import json
        records = json.loads(records)

    # Use API's `latest` field instead of calculating from possibly-truncated records
    api_latest = body_data.get("res", {}).get("latest", {}) or body_data.get("latest", {})
    if isinstance(api_latest, str):
        import json
        api_latest = json.loads(api_latest)

    # Merge Apple Health data (只用于 chart, 不影响 latest)
    records_merged, ah_dates = _merge_apple_health(records, start, today)

    summary = summarize_body(records_merged)
    stats = body_stats(records_merged)
    changes = body_changes(records_merged)

    # Latest weight: 统一从 records 计算（不受 API latest 范围限制影响）
    latest = body_latest(records_merged)

    latest_weight_val = latest.get("weight", {}).get("value")

    config = _load_body_config()
    height_cm = config.get("height_cm", 170)
    bmi = calculate_bmi(latest_weight_val, height_cm)

    # Latest change for stat cards
    weight_recs = [r for r in records_merged if r.get("type") == "weight"]
    bf_recs = [r for r in records_merged if r.get("type") == "bodyfat"]
    latest_weight_change = None
    latest_bf_change = None
    if weight_recs and len(weight_recs) > 1:
        key = f"weight:{weight_recs[0].get('datestr','')}"
        latest_weight_change = changes.get(key)
    if bf_recs and len(bf_recs) > 1:
        key = f"bodyfat:{bf_recs[0].get('datestr','')}"
        latest_bf_change = changes.get(key)

    # Latest change for body measurement types
    body_fields = ['chest','weist','bot','shoulder','neck',
                   'arm_left','arm_right','forearm_left','forearm_right',
                   'leg_left','leg_right','cav_left','cav_right']
    latest_measurement_changes = {}
    for field in body_fields:
        recs = [r for r in records_merged if r.get("type") == field]
        if recs and len(recs) > 1:
            key = f"{field}:{recs[0].get('datestr','')}"
            val = changes.get(key)
            if val is not None:
                latest_measurement_changes[field] = val
            else:
                latest_measurement_changes[field] = 0.0
        elif recs:
            latest_measurement_changes[field] = 0.0
    body_fields = ['chest','weist','bot','shoulder','neck',
                   'arm_left','arm_right','forearm_left','forearm_right',
                   'leg_left','leg_right','cav_left','cav_right']
    latest_measurement_changes = {}
    for field in body_fields:
        recs = [r for r in records_merged if r.get("type") == field]
        if recs and len(recs) > 1:
            key = f"{field}:{recs[0].get('datestr','')}"
            val = changes.get(key)
            if val is not None:
                latest_measurement_changes[field] = val
            else:
                latest_measurement_changes[field] = 0.0
        elif recs:
            latest_measurement_changes[field] = 0.0

    # Period change (first vs last in range)
    w_chrono = sorted(
        [r for r in records_merged if r.get("type") == "weight" and r.get("value") is not None],
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
        [r for r in records_merged if r.get("type") == "bodyfat" and r.get("value") is not None],
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
    ah_source = len(ah_dates) > 0  # 是否有 Apple Health 数据

    return 
    freshness_svc = DataFreshnessService()
    freshness_ctx = freshness_svc.get_freshness_context()
HTMLResponse(tmpl.render(
        request=request,
        freshness=freshness_ctx,
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
        latest_measurement_changes=latest_measurement_changes,
        period_weight_change=period_weight_change,
        period_bf_change=period_bf_change,
        height_cm=height_cm,
        records_data=_build_table_data(records_merged, changes, ah_dates, height_cm),
        ah_source=ah_source,
        ah_dates=ah_dates,
        today=date.today().isoformat(),
        
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



def _build_table_data(records: list[dict], changes: dict[str, float], ah_dates: set = None, height_cm: float = 170.0) -> list[dict]:
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
                bmi_val = calculate_bmi(float(v), height_cm)
                if bmi_val is not None:
                    by_date[d]["bmi"] = bmi_val
            # Track source
            src = rec.get("source", "")
            if src == "apple_health":
                by_date[d]["source"] = "🍎"
        # Mark Apple Health source for dates with any AH data
    for d in by_date:
        if ah_dates and d in ah_dates and "source" not in by_date[d]:
            by_date[d]["source"] = "🍎"
    rows = sorted(by_date.values(), key=lambda x: x["date"], reverse=True)
    return rows
