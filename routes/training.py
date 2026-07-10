from __future__ import annotations
import calendar
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app_state import get_jinja_env, get_data_service, get_cache
from analysis import (
    summarize_training,
    movement_history,
    training_frequency_by_date,
    training_duration_trend,
    calories_trend,
    heart_rate_trend,
    training_type_distribution,
)
from movements_cn import apply_movement_cn_to_trains

router = APIRouter(tags=["training"])


def _parse_date_from_cache_key(key: str) -> str | None:
    """Extract date string from cache key like 'training:2026-04-02:False'."""
    parts = key.split(":")
    if len(parts) >= 2 and parts[0] == "training":
        # The date is always the second part
        d = parts[1]
        try:
            datetime.strptime(d, "%Y-%m-%d")
            return d
        except ValueError:
            return None
    return None


def _get_cached_training_dates() -> list[str]:
    """Get dates with NON-EMPTY cached training data, sorted."""
    cache = get_cache()
    keys = cache.keys_by_prefix("training:")
    dates: set[str] = set()
    for k in keys:
        d = _parse_date_from_cache_key(k)
        if not d:
            continue
        cached = cache.get(k)
        if not cached:
            continue
        trains = cached.get("res", {}).get("trains", []) or cached.get("trains", [])
        if trains:
            dates.add(d)
    return sorted(dates)



def _merge_overlapping_trains(trains: list[dict]) -> list[dict]:
    """Merge training sessions that overlap in time (within 15 min gap).
    Combines movements and preserves Apple Health cardio metrics.
    """
    if not trains:
        return []
    sorted_trains = sorted(trains, key=lambda t: t.get("start", 0))
    merged = []
    current = dict(sorted_trains[0])
    current["_cardio_metrics"] = {}

    for t in sorted_trains[1:]:
        gap = t.get("start", 0) - current.get("end", 0)
        if gap < 15 * 60 * 1000:  # 15 min overlap window
            # Merge if one is manual + other is Apple Health (cardio)
            cur_has_manual = any(m.get("exetype") != "cardio" for m in current.get("movements", []))
            cur_has_cardio = any(m.get("exetype") == "cardio" for m in current.get("movements", []))
            t_has_manual = any(m.get("exetype") != "cardio" for m in t.get("movements", []))
            t_has_cardio = any(m.get("exetype") == "cardio" for m in t.get("movements", []))
            if (cur_has_manual and t_has_cardio) or (t_has_manual and cur_has_cardio):
                current["end"] = max(current.get("end", 0), t.get("end", 0))
                current["start"] = min(current.get("start", 0), t.get("start", 0))
                current.setdefault("movements", []).extend(t.get("movements", []))
                # Set total session duration
                if not current["_cardio_metrics"]:
                    dur_min = round((current["end"] - current["start"]) / 60000, 0)
                    current["_cardio_metrics"]["total_duration_min"] = dur_min
                # Extract cardio metrics from whichever train has them
                for src in (current, t):
                    for m in src.get("movements", []):
                        if m.get("exetype") == "cardio":
                            for s in m.get("sets", []):
                                cal = s.get("calories") or (s.get("metrics", {}) or {}).get("calories")
                                ahr = s.get("avgHeartRate") or (s.get("metrics", {}) or {}).get("avgHeartRate")
                                mhr = s.get("maxHeartRate") or (s.get("metrics", {}) or {}).get("maxHeartRate")
                                dur = s.get("time") or s.get("workoutTime")
                                if cal or ahr:
                                    current["_cardio_metrics"].update({
                                        "calories": cal, "avgHeartRate": ahr,
                                        "maxHeartRate": mhr, "duration_s": dur,
                                    })
        else:
            # Remove cardio movements from merged train (data in _cardio_metrics)
            if current["_cardio_metrics"]:
                current["movements"] = [m for m in current.get("movements", [])
                                        if m.get("exetype") != "cardio"]
            merged.append(current)
            current = dict(t)
            current["_cardio_metrics"] = {}
    if current["_cardio_metrics"]:
        current["movements"] = [m for m in current.get("movements", [])
                                if m.get("exetype") != "cardio"]
    merged.append(current)
    return merged

def _compute_day_intensities() -> dict[str, float]:
    """Compute training intensity (0-1) per day based on duration + calories."""
    cache = get_cache()
    dates = _get_cached_training_dates()
    raw: dict[str, float] = {}
    for d in dates:
        cached = cache.get(f"training:{d}:True") or cache.get(f"training:{d}:False")
        if not cached:
            continue
        trains = cached.get("res", {}).get("trains", []) or cached.get("trains", [])
        score = 0.0
        for train in trains:
            dur = (train.get("end", 0) - train.get("start", 0)) / 60000
            cal = 0.0
            for mov in train.get("movements", []):
                for s in mov.get("sets", []):
                    try:
                        c = s.get("calories") or (s.get("metrics", {}) or {}).get("calories")
                        if c: cal += float(c)
                    except (ValueError, TypeError):
                        pass
            score += dur * 0.5 + cal * 0.02
        if score > 0:
            raw[d] = score
    max_s = max(raw.values()) if raw else 1
    return {d: round(v / max_s, 2) for d, v in raw.items()}


def _build_calendar(year: int, month: int, active_dates: set[str], intensities: dict[str, float] | None = None) -> dict:
    """Build calendar grid data for a month.
    Returns dict with year, month, month_name, weeks (list of weeks),
    where each week is a list of day dicts with keys: day, has_data, is_today.
    """
    cal = calendar.Calendar()
    month_days = cal.monthdays2calendar(year, month)
    today_str = date.today().isoformat()

    weeks = []
    for week in month_days:
        w = []
        for day_num, weekday in week:
            if day_num == 0:
                w.append({"day": 0, "date_str": "", "has_data": False, "is_today": False, "intensity": 0})
            else:
                ds = f"{year:04d}-{month:02d}-{day_num:02d}"
                w.append({
                    "day": day_num,
                    "date_str": ds,
                    "has_data": ds in active_dates,
                    "is_today": ds == today_str,
                    "intensity": intensities.get(ds, 0) if intensities else 0,
                })
        weeks.append(w)

    # Previous month
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    # Next month
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1

    return {
        "year": year,
        "month": month,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
        "month_name": ["", "一月", "二月", "三月", "四月", "五月", "六月",
                        "七月", "八月", "九月", "十月", "十一月", "十二月"][month],
        "weeks": weeks,
    }


# Order matters: specific routes before parameterized ones
@router.get("/training/calendar/{year}/{month}", response_class=HTMLResponse)
async def training_calendar_fragment(request: Request, year: int, month: int):
    """HTMX fragment: returns calendar HTML for a given month."""
    if month < 1 or month > 12:
        month = date.today().month
    active_dates = set(_get_cached_training_dates())
    day_intensity = _compute_day_intensities()
    cal_data = _build_calendar(year, month, active_dates, day_intensity)
    env = get_jinja_env()
    tmpl = env.get_template("training_calendar.html")
    return HTMLResponse(tmpl.render(request=request, cal=cal_data))


@router.get("/training/{dt}", response_class=HTMLResponse)
async def training_detail_fragment(request: Request, dt: str):
    """HTMX fragment: returns training detail for a given date."""
    ds = get_data_service()
    try:
        data = await ds.get_training(dt, full=True)
    except Exception:
        data = {"res": {"trains": []}}
    trains = data.get("res", {}).get("trains", [])
    if not trains:
        trains = data.get("trains", [])
    trains = apply_movement_cn_to_trains(trains)
    trains = _merge_overlapping_trains(trains)
    summary = summarize_training(trains)
    env = get_jinja_env()
    tmpl = env.get_template("training_detail.html")
    return HTMLResponse(tmpl.render(
        request=request, summary=summary, trains=trains, date_str=dt
    ))


@router.get("/training", response_class=HTMLResponse)
async def training_page(request: Request):
    ds = get_data_service()
    today = date.today()
    today_str = today.isoformat()

    # Warm cache: fetch recent dates so charts have data
    warm_dates = set()
    for i in range(14):
        d = (today - timedelta(days=i)).isoformat()
        try:
            await ds.get_training(d, full=True)
            warm_dates.add(d)
        except Exception:
            pass

    # Calendar data (after warming so markers are accurate)
    active_dates = set(_get_cached_training_dates())
    day_intensity = _compute_day_intensities()
    cal_data = _build_calendar(today.year, today.month, active_dates, day_intensity)

    # Today's training detail
    today_data = {}
    try:
        today_data = await ds.get_training(today_str, full=True)
    except Exception:
        pass
    today_trains = today_data.get("res", {}).get("trains", [])
    if not today_trains:
        today_trains = today_data.get("trains", [])
    today_trains = apply_movement_cn_to_trains(today_trains)
    today_trains = _merge_overlapping_trains(today_trains)
    today_summary = summarize_training(today_trains)

    # Charts data: load last 90 days from cache
    all_trains = []
    for d in sorted(active_dates):
        d_dt = datetime.strptime(d, "%Y-%m-%d").date()
        if (today - d_dt).days <= 90:
            cached = get_cache().get(f"training:{d}:False") or get_cache().get(f"training:{d}:True")
            if cached:
                cached_trains = cached.get("res", {}).get("trains", [])
                if not cached_trains:
                    cached_trains = cached.get("trains", [])
                all_trains.extend(cached_trains)

    all_trains = apply_movement_cn_to_trains(all_trains)
    all_trains_raw = list(all_trains)
    all_trains = _merge_overlapping_trains(all_trains)

    from analysis import training_volume_by_date
    vol_data = training_volume_by_date(all_trains)
    freq_dates = training_frequency_by_date(all_trains)
    freq_counts: dict[str, int] = {}
    for t in all_trains:
        d = t.get("datestr", "")
        if d:
            freq_counts[d] = freq_counts.get(d, 0) + 1
    analysis_data = {
        "duration": training_duration_trend(all_trains),
        "calories": calories_trend(all_trains),
        "heart_rate": heart_rate_trend(all_trains),
        "type_dist": training_type_distribution(all_trains_raw, cardio_only=True),
    }

    # Unique movement names for the movement tracker dropdown
    movement_names = sorted(set(
        mov.get("name", "")
        for train in all_trains
        for mov in train.get("movements", [])
        if mov.get("name")
    ))

    env = get_jinja_env()
    tmpl = env.get_template("training.html")
    return HTMLResponse(tmpl.render(
        request=request,
        cal=cal_data,
        date_str=today_str,
        trains=today_trains,
        summary=today_summary,
        volume_data=vol_data,
        analysis_data=analysis_data,
        freq_dates=freq_dates,
        freq_counts=freq_counts,
        movement_names=movement_names,
    ))


@router.get("/training/track/{movement_name}")
async def training_track(movement_name: str):
    """Returns movement history JSON for Chart.js."""
    from analysis import movement_history
    cache = get_cache()
    cached_dates = _get_cached_training_dates()
    all_trains = []
    for d in cached_dates:
        cached = cache.get(f"training:{d}:False")
        if cached:
            trains = cached.get("res", {}).get("trains", [])
            if not trains:
                trains = cached.get("trains", [])
            all_trains.extend(trains)
        else:
            cached = cache.get(f"training:{d}:True")
            if cached:
                trains = cached.get("res", {}).get("trains", [])
                if not trains:
                    trains = cached.get("trains", [])
                all_trains.extend(trains)
    all_trains = apply_movement_cn_to_trains(all_trains)
    all_trains_raw = list(all_trains)
    all_trains = _merge_overlapping_trains(all_trains)
    hist = movement_history(all_trains, movement_name)
    return {
        "dates": [h["date"] for h in hist],
        "max_weight": [h["max_weight"] for h in hist],
        "total_volume": [h["total_volume"] for h in hist],
        "total_sets": [h["total_sets"] for h in hist],
    }
