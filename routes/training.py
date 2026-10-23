from __future__ import annotations
import asyncio
import calendar
import logging
from contextlib import suppress
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import HTMLResponse
from app_state import get_jinja_env, get_data_service, get_cache
from data_service import DataService, extract_trains, get_cached_training, resolve_training_cache
from analysis import (
    summarize_training,
    movement_history,
    training_frequency_by_date,
    classify_bodypart,
    session_bounds,
    session_metrics,
    summarize_set_metrics,
    iter_movement_sets,
)
from movements_cn import apply_movement_cn_to_trains
from training_charts import build_chart_ranges, select_training_window
from training_records import is_training_date

router = APIRouter(tags=["training"])
logger = logging.getLogger(__name__)

_history_backfill_task: asyncio.Task | None = None
_EMPTY_TRAINING_CACHE_TTL = timedelta(minutes=30)
_EMPTY_TRAINING_RECHECK_DAYS = 7


def compute_frequency_stats(freq_counts: dict[str, int], today: date | None = None) -> dict:
    """Compute 90-day frequency stats using real calendar adjacency."""
    today = today or date.today()
    start = today - timedelta(days=89)
    counts = {
        date.fromisoformat(day): int(count)
        for day, count in freq_counts.items()
        if count and start <= date.fromisoformat(day) <= today
    }
    training_dates = sorted(counts)
    total = sum(counts.values())

    longest = 0
    running = 0
    previous = None
    for training_date in training_dates:
        if previous is not None and training_date == previous + timedelta(days=1):
            running += 1
        else:
            running = 1
        longest = max(longest, running)
        previous = training_date

    current = 0
    if training_dates and training_dates[-1] in {today, today - timedelta(days=1)}:
        cursor = training_dates[-1]
        dates_set = set(training_dates)
        while cursor in dates_set:
            current += 1
            cursor -= timedelta(days=1)

    return {
        "total": total,
        "days": len(training_dates),
        "longest_streak": longest,
        "current_streak": current,
        "weekly_average": round(total / (90 / 7), 1),
        "rest_days": 90 - len(training_dates),
    }


def _cached_training_has_data(data: object) -> bool:
    return bool(extract_trains(data))


def _should_skip_training_backfill(cache, training_date: str, now: datetime) -> bool:
    """Keep populated caches; recheck only recent empty entries after their TTL."""
    cached, mode = resolve_training_cache(cache, training_date)
    if cached is None:
        return False
    if not DataService._is_valid_training_response(cached):
        return False
    if extract_trains(cached):
        return mode is True
    if (now.date() - date.fromisoformat(training_date)).days > _EMPTY_TRAINING_RECHECK_DAYS:
        return True
    mtime = cache.get_mtime(f"training:{training_date}:{mode}")
    if not mtime:
        return True
    try:
        cached_at = datetime.fromisoformat(mtime.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=timezone.utc)
    return now - cached_at <= _EMPTY_TRAINING_CACHE_TTL


async def _backfill_training_history(
    ds,
    cache,
    *,
    days: int = 60,
    delay_seconds: float = 6.0,
    today: date | None = None,
    now: datetime | None = None,
) -> None:
    """Fill missing recent training cache entries one request at a time."""
    # API dates follow the dashboard's local calendar; UTC is only an instant.
    current_time = (now or datetime.now(timezone.utc)).astimezone()
    end_date = today or current_time.date()
    for offset in range(days):
        training_date = (end_date - timedelta(days=offset)).isoformat()
        if _should_skip_training_backfill(cache, training_date, current_time):
            continue
        try:
            await ds.get_training(training_date, full=True, force_refresh=True)
        except Exception:
            logger.exception("Training history backfill failed for %s", training_date)
        await asyncio.sleep(delay_seconds)


def _ensure_training_history_backfill(ds, cache) -> None:
    """Start one shared background history backfill task when needed."""
    global _history_backfill_task
    if _history_backfill_task is None or _history_backfill_task.done():
        _history_backfill_task = asyncio.create_task(
            _backfill_training_history(ds, cache)
        )


async def stop_training_background_tasks() -> None:
    """Cancel and join the process-local training history job."""
    global _history_backfill_task
    task = _history_backfill_task
    _history_backfill_task = None
    if task is None or task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def _parse_date_from_cache_key(key: str) -> str | None:
    """Extract date string from cache key like 'training:2026-04-02:False'."""
    parts = key.split(":")
    if len(parts) >= 2 and parts[0] == "training":
        # The date is always the second part
        d = parts[1]
        if is_training_date(d):
            return d
    return None


def _get_cached_training_dates() -> list[str]:
    """Get dates with NON-EMPTY cached training data, sorted."""
    cache = get_cache()
    days = {_parse_date_from_cache_key(key) for key in cache.keys_by_prefix("training:")}
    return sorted(day for day in days if day and extract_trains(get_cached_training(cache, day)))



def _merge_overlapping_trains(trains: list[dict]) -> list[dict]:
    """Merge training sessions that overlap in time (within 15 min gap).
    Combines movements and preserves Apple Health cardio metrics.
    """
    if not trains:
        return []
    sorted_trains = sorted(trains, key=lambda t: (session_bounds(t) or (float("inf"), 0))[0])

    def finish(train):
        if train["_cardio_metrics"]:
            cardio = [m for m in train.get("movements", []) if m.get("exetype") == "cardio"]
            metrics = summarize_set_metrics(s for m in cardio for s in iter_movement_sets(m))
            metrics["total_duration_min"] = train["_cardio_metrics"]["total_duration_min"]
            train["_cardio_metrics"] = metrics
            train["_cardio_movements"] = cardio
            train["movements"] = [m for m in train.get("movements", []) if m.get("exetype") != "cardio"]
        return train
    merged = []
    current = dict(sorted_trains[0])
    current["_cardio_metrics"] = {}

    for t in sorted_trains[1:]:
        cur_has_manual = any(m.get("exetype") != "cardio" for m in current.get("movements", []))
        cur_has_cardio = any(m.get("exetype") == "cardio" for m in current.get("movements", []))
        t_has_manual = any(m.get("exetype") != "cardio" for m in t.get("movements", []))
        t_has_cardio = any(m.get("exetype") == "cardio" for m in t.get("movements", []))
        compatible = (cur_has_manual and t_has_cardio) or (t_has_manual and cur_has_cardio)
        same_date = current.get("datestr") == t.get("datestr")
        current_bounds, incoming_bounds = session_bounds(current), session_bounds(t)
        gap = incoming_bounds[0] - current_bounds[1] if current_bounds and incoming_bounds else None
        if gap is not None and same_date and gap < 15 * 60 * 1000 and compatible:
            current["end"] = max(current.get("end", 0), t.get("end", 0))
            current["start"] = min(current.get("start", 0), t.get("start", 0))
            current["movements"] = current.get("movements", []) + t.get("movements", [])
            # Set total session duration
            dur_min = round((current["end"] - current["start"]) / 60000, 0)
            current["_cardio_metrics"]["total_duration_min"] = dur_min
        else:
            merged.append(finish(current))
            current = dict(t)
            current["_cardio_metrics"] = {}
    merged.append(finish(current))
    return merged

def _compute_day_intensities() -> dict[str, float]:
    """Compute training intensity (0-1) per day based on duration + calories."""
    cache = get_cache()
    dates = _get_cached_training_dates()
    raw: dict[str, float] = {}
    for d in dates:
        cached = get_cached_training(cache, d, full=True)
        if not cached:
            continue
        trains = extract_trains(cached)
        score = 0.0
        for train in trains:
            bounds = session_bounds(train)
            dur = (bounds[1] - bounds[0]) / 60000 if bounds else 0
            cal = session_metrics(train)["calories"]
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
        "can_prev": prev_year >= date.min.year,
        "can_next": next_year <= date.max.year,
        "month_name": ["", "一月", "二月", "三月", "四月", "五月", "六月",
                        "七月", "八月", "九月", "十月", "十一月", "十二月"][month],
        "weeks": weeks,
    }


# Order matters: specific routes before parameterized ones
@router.get("/training/calendar/{year}/{month}", response_class=HTMLResponse)
async def training_calendar_fragment(
    request: Request,
    year: int = Path(..., ge=1, le=9999),
    month: int = Path(..., ge=1, le=12),
):
    """HTMX fragment: returns calendar HTML for a given month."""
    active_dates = set(_get_cached_training_dates())
    day_intensity = _compute_day_intensities()
    cal_data = _build_calendar(year, month, active_dates, day_intensity)
    env = get_jinja_env()
    tmpl = env.get_template("training_calendar.html")
    return HTMLResponse(tmpl.render(request=request, cal=cal_data))


def _cardio_summary(trains: list[dict]) -> dict:
    metrics = [session_metrics(train, cardio_only=True) for train in trains]
    return {
        "minutes": round(sum(row["duration_s"] for row in metrics) / 60),
        "distance": round(sum(row["distance"] for row in metrics), 2),
        "calories": round(sum(row["calories"] for row in metrics)),
    }


@router.get("/training/{dt}", response_class=HTMLResponse)
async def training_detail_fragment(request: Request, dt: str):
    """HTMX fragment: returns training detail for a given date."""
    if not is_training_date(dt):
        raise HTTPException(status_code=422, detail="训练日期须为有效的 YYYY-MM-DD 日期")
    ds = get_data_service()
    try:
        data = await ds.get_training(dt, full=True)
    except Exception:
        logger.exception("Training detail fetch failed for %s", dt)
        data = {"res": {"trains": []}}
    trains = extract_trains(data)
    trains = apply_movement_cn_to_trains(trains)
    trains = _merge_overlapping_trains(trains)
    summary = summarize_training(trains)
    active_dates = sorted(_get_cached_training_dates())
    previous_date = None
    next_date = None
    if dt in active_dates:
        index = active_dates.index(dt)
        previous_date = active_dates[index - 1] if index > 0 else None
        next_date = active_dates[index + 1] if index < len(active_dates) - 1 else None
    env = get_jinja_env()
    tmpl = env.get_template("training_detail.html")
    return HTMLResponse(tmpl.render(
        request=request,
        summary=summary,
        trains=trains,
        date_str=dt,
        session_count=len(trains),
        previous_date=previous_date,
        next_date=next_date,
        cardio_summary=_cardio_summary(trains),
    ))


@router.get("/training", response_class=HTMLResponse)
async def training_page(request: Request):
    ds = get_data_service()
    cache = get_cache()
    today = date.today()
    today_str = today.isoformat()

    # Calendar data from the cache currently available
    active_dates = set(_get_cached_training_dates())
    day_intensity = _compute_day_intensities()
    cal_data = _build_calendar(today.year, today.month, active_dates, day_intensity)

    # Today's training detail
    today_data = {}
    try:
        today_data = await ds.get_training(today_str, full=True)
    except Exception:
        logger.exception("Today's training fetch failed for %s", today_str)
    _ensure_training_history_backfill(ds, cache)
    today_trains = extract_trains(today_data)
    today_trains = apply_movement_cn_to_trains(today_trains)
    today_trains = _merge_overlapping_trains(today_trains)
    today_summary = summarize_training(today_trains)

    # Charts data: load last 90 days from cache
    all_trains = []
    for d in sorted(active_dates):
        d_dt = datetime.strptime(d, "%Y-%m-%d").date()
        if 0 <= (today - d_dt).days < 90:
            cached = get_cached_training(cache, d)
            if cached:
                cached_trains = extract_trains(cached)
                all_trains.extend(cached_trains)

    all_trains = select_training_window(all_trains, today, 90)
    all_trains = apply_movement_cn_to_trains(all_trains)
    all_trains_raw = list(all_trains)
    all_trains = _merge_overlapping_trains(all_trains)

    vol_data, analysis_data = build_chart_ranges(all_trains, all_trains_raw, today)
    freq_dates = training_frequency_by_date(all_trains)
    freq_counts: dict[str, int] = {}
    for t in all_trains:
        d = t.get("datestr", "")
        if d:
            freq_counts[d] = freq_counts.get(d, 0) + 1
    freq_stats = compute_frequency_stats(freq_counts, today=today)

    # Unique movement names for the movement tracker dropdown
    EXCLUDED_MOVEMENTS = {"力量训练", "传统力量训练", "功能性力量训练", "步行"}
    movement_names = sorted(set(
        mov.get("name", "")
        for train in all_trains
        for mov in train.get("movements", [])
        if mov.get("name")
    ) - EXCLUDED_MOVEMENTS)
    movement_options = [
        {"name": name, "group": classify_bodypart(name)}
        for name in movement_names
    ]

    env = get_jinja_env()
    tmpl = env.get_template("training.html")
    return HTMLResponse(tmpl.render(
        request=request,
        cal=cal_data,
        date_str=today_str,
        trains=today_trains,
        summary=today_summary,
        cardio_summary=_cardio_summary(today_trains),
        volume_data=vol_data,
        analysis_data=analysis_data,
        freq_dates=freq_dates,
        freq_counts=freq_counts,
        freq_stats=freq_stats,
        movement_names=movement_names,
        movement_options=movement_options,
    ))


@router.get("/training/track/{movement_name:path}")
async def training_track(movement_name: str):
    """Returns movement history JSON for Chart.js."""
    from analysis import movement_history
    cache = get_cache()
    cached_dates = _get_cached_training_dates()
    all_trains = []
    for d in cached_dates:
        all_trains.extend(extract_trains(get_cached_training(cache, d)))
    all_trains = apply_movement_cn_to_trains(all_trains)
    all_trains_raw = list(all_trains)
    all_trains = _merge_overlapping_trains(all_trains)
    hist = movement_history(all_trains, movement_name)
    return {
        "dates": [h["date"] for h in hist],
        "max_weight": [h["max_weight"] for h in hist],
        "total_volume": [h["total_volume"] for h in hist],
        "total_sets": [h["total_sets"] for h in hist],
        "estimated_1rm": [h.get("estimated_1rm", 0) for h in hist],
    }
