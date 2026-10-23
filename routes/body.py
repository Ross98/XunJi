from datetime import date, timedelta

import json
import logging
import math
import os

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from app_state import get_cache, get_jinja_env, get_data_service
from data_service import DataFreshnessService, DataService, extract_body_records, filter_body_records
from analysis import summarize_body, body_latest, body_stats, body_changes, calculate_bmi
from local_settings import write_json_atomic

router = APIRouter(tags=["body"])
logger = logging.getLogger(__name__)

BODY_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "body_config.json")


def _load_body_config() -> dict:
    config = {}
    if os.path.exists(BODY_CONFIG_PATH):
        try:
            with open(BODY_CONFIG_PATH, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                config = saved
        except (OSError, ValueError):
            logger.exception("Unable to load body preferences")
    try:
        height = float(config.get("height_cm", 170))
        if not 100 <= height <= 250:
            raise ValueError("Height outside supported range")
    except (TypeError, ValueError, OverflowError):
        height = 170
    config["height_cm"] = height
    return config


def _save_body_config(config: dict):
    write_json_atomic(BODY_CONFIG_PATH, config)

RANGE_MAP = {
    "30": 30,
    "90": 90,
    "180": 180,
    "365": 365,
    "all": None,
}


def _merge_apple_health(records: list[dict], start: str, end: str) -> tuple[list[dict], set]:
    """将 Apple Health 身体数据合并到训记记录中。
    返回 (合并后的记录, 有 Apple Health 数据的日期集合)。
    """
    import apple_health as ah

    records = [record for record in filter_body_records(records)
               if start <= record["datestr"] <= end]

    ah_weight = ah.query_records_desc("HKQuantityTypeIdentifierBodyMass", start, end)
    ah_bf = ah.query_records_desc("HKQuantityTypeIdentifierBodyFatPercentage", start, end)

    if not ah_weight and not ah_bf:
        return sorted(filter_body_records(records), key=lambda row: row.get("datestr", ""), reverse=True), set()

    ah_dates = set()
    # Convert Apple Health records to 训记 format, merge by date
    # Key: date string -> {type: value}
    ah_by_date: dict[str, dict] = {}

    for metric, samples, scale, precision in (
        ("weight", ah_weight, 1, 2),
        ("bodyfat", ah_bf, 100, 1),
    ):
        for row in samples:
            day = row["start_date"][:10]
            candidate = {"datestr": day, "type": metric, "value": row.get("value")}
            if not filter_body_records([candidate]):
                continue
            value = float(candidate["value"]) * scale
            if not math.isfinite(value):
                continue
            ah_dates.add(day)
            values = ah_by_date.setdefault(day, {})
            # Inputs are newest first; invalid samples must not claim the key.
            if metric not in values:
                values[metric] = round(value, precision)

    merged = list(records)
    existing_keys = {(record.get("datestr"), record.get("type")) for record in records}

    for d, vals in ah_by_date.items():
        for typ, val in vals.items():
            if val is None:
                continue
            # Skip if 训记 already has this type on this date
            if (d, typ) not in existing_keys:
                merged.append({
                    "datestr": d,
                    "type": typ,
                    "value": str(val),
                    "source": "apple_health",
                })

    merged = sorted(filter_body_records(merged), key=lambda row: row.get("datestr", ""), reverse=True)
    return merged, ah_dates



@router.get("/body", response_class=HTMLResponse)
async def body_page(
    request: Request,
    range: str = Query("90", description="Days range: 30, 90, 180, 365, all"),
):
    ds = get_data_service()
    today_date = date.today()
    today = today_date.isoformat()
    range = range if range in RANGE_MAP else "90"
    days = RANGE_MAP[range]
    start = (today_date - timedelta(days=days - 1)).isoformat() if days is not None else date.min.isoformat()

    body_warning = None
    try:
        # A null API start requests all history; local SQLite uses an inclusive
        # lower bound across its full supported calendar.
        body_data = await ds.get_body(start if days is not None else None, today)
    except Exception:
        logger.exception("Body API unavailable; rendering Apple Health data only")
        body_data = {"res": {"records": []}}
        body_warning = "训记暂时无法读取，当前仅展示 Apple Health 记录。稍后刷新重试。"
    if isinstance(body_data, dict) and body_data.get("_cache_fallback"):
        body_warning = "训记暂时无法读取，当前展示本机缓存与 Apple Health 记录，可能缺少近期数据。稍后刷新重试。"
    elif not DataService._is_valid_body_response(body_data):
        body_warning = "训记暂时无法读取，当前仅展示 Apple Health 记录。稍后刷新重试。"
    records = extract_body_records(body_data)

    # Merge Apple Health data into gaps in the XunJi records.
    records_merged, ah_dates = _merge_apple_health(records, start, today)

    summary = summarize_body(records_merged)
    stats = body_stats(records_merged)
    changes = body_changes(records_merged)

    # Latest weight: 统一从 records 计算（不受 API latest 范围限制影响）
    latest = body_latest(records_merged)
    for metric, item in latest.items():
        matching = next((
            record for record in records_merged
            if record.get("type") == metric and record.get("datestr") == item.get("date")
        ), None)
        item["source"] = "Apple Health" if matching and matching.get("source") == "apple_health" else "训记"

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
        if recs:
            key = f"{field}:{recs[0].get('datestr','')}"
            latest_measurement_changes[field] = changes.get(key)
    measurement_dates = [
        latest[field]["date"] for field in body_fields
        if latest.get(field, {}).get("date")
    ]
    latest_measurement_date = max(measurement_dates) if measurement_dates else None

    asymmetry = []
    for label, right_key, left_key in (
        ("大臂", "arm_right", "arm_left"),
        ("小臂", "forearm_right", "forearm_left"),
        ("大腿", "leg_right", "leg_left"),
        ("小腿", "cav_right", "cav_left"),
    ):
        right = latest.get(right_key, {}).get("value")
        left = latest.get(left_key, {}).get("value")
        if right is None or left is None:
            continue
        try:
            right_value, left_value = float(right), float(left)
        except (TypeError, ValueError):
            continue
        right_date = latest[right_key].get("date")
        left_date = latest[left_key].get("date")
        comparable = bool(right_date and right_date == left_date)
        average = (right_value + left_value) / 2
        difference = round(right_value - left_value, 1) if comparable else None
        asymmetry.append({
            "label": label,
            "right": right_value,
            "left": left_value,
            "right_date": right_date,
            "left_date": left_date,
            "difference": difference,
            "percent": (round(abs(difference) / average * 100, 1) if average else 0) if comparable else None,
            "dominant": ("右侧" if difference > 0 else "左侧" if difference < 0 else "平衡") if comparable else None,
        })
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
    ah_source = bool(ah_dates)

    freshness_svc = DataFreshnessService(xunji_cache=get_cache())
    freshness_ctx = freshness_svc.get_freshness_context()
    return HTMLResponse(tmpl.render(
        request=request,
        body_warning=body_warning,
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
        latest_measurement_date=latest_measurement_date,
        asymmetry=asymmetry,
        period_weight_change=period_weight_change,
        period_bf_change=period_bf_change,
        height_cm=height_cm,
        records_data=_build_table_data(records_merged, changes, ah_dates, height_cm),
        ah_source=ah_source,
        ah_dates=ah_dates,
        today=today,
        
    ))


@router.post("/body/settings")
async def update_body_settings(data: dict):
    config = _load_body_config()
    if "height_cm" in data:
        h = data["height_cm"]
        if isinstance(h, (int, float)) and 100 <= h <= 250:
            config["height_cm"] = round(float(h), 1)
        else:
            return {"status": "error", "message": "身高需在100–250 cm之间"}
    try:
        _save_body_config(config)
    except OSError:
        logger.exception("Unable to save body preferences")
        return {"status": "error", "message": "身高保存失败，请检查文件权限或磁盘空间后重试"}
    return {"status": "ok"}



def _build_table_data(records: list[dict], changes: dict[str, float], ah_dates: set = None, height_cm: float = 170.0) -> list[dict]:
    """Merge weight and bodyfat records by date into table rows (newest first)."""
    by_date: dict[str, dict] = {}
    for rec in filter_body_records(records):
        d, t, v = rec["datestr"], rec["type"], float(rec["value"])
        if t not in {"weight", "bodyfat"}:
            continue
        by_date.setdefault(d, {"date": d})
        by_date[d][t] = round(v, 2)
        by_date[d][f"{t}_change"] = changes.get(f"{t}:{d}")
        if t == "weight":
            bmi_val = calculate_bmi(v, height_cm)
            if bmi_val is not None:
                by_date[d]["bmi"] = bmi_val
        src = rec.get("source", "")
        by_date[d][f"{t}_source"] = "Apple Health" if src == "apple_health" else "训记"
    for d in by_date:
        if ah_dates and d in ah_dates:
            by_date[d].setdefault("source", "Apple Health")
    rows = sorted(by_date.values(), key=lambda x: x["date"], reverse=True)
    return rows
