from __future__ import annotations
import asyncio
import calendar
import logging
from contextlib import suppress
from datetime import date, datetime, timedelta, timezone

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
    classify_bodypart,
)
from movements_cn import apply_movement_cn_to_trains

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
    if not isinstance(data, dict):
        return False
    res = data.get("res")
    if isinstance(res, dict) and isinstance(res.get("trains"), list):
        return bool(res["trains"])
    return isinstance(data.get("trains"), list) and bool(data["trains"])


def _should_skip_training_backfill(cache, training_date: str, now: datetime) -> bool:
    """Keep populated caches; recheck only recent empty entries after their TTL."""
    keys = (
        f"training:{training_date}:True",
        f"training:{training_date}:False",
    )
    empty_entries = []
    for key in keys:
        cached = cache.get(key)
        if cached is None:
            continue
        if _cached_training_has_data(cached):
            return True
        empty_entries.append(key)

    if not empty_entries:
        return False
    if (now.date() - date.fromisoformat(training_date)).days > _EMPTY_TRAINING_RECHECK_DAYS:
        return True

    for key in empty_entries:
        mtime = cache.get_mtime(key)
        if not mtime:
            return True
        try:
            cached_at = datetime.fromisoformat(mtime)
        except ValueError:
            return True
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        if now - cached_at <= _EMPTY_TRAINING_CACHE_TTL:
            return True
    return False


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
    current_time = now or datetime.now(timezone.utc)
    end_date = today or current_time.date()
    for offset in range(days):
        training_date = (end_date - timedelta(days=offset)).isoformat()
        if _should_skip_training_backfill(cache, training_date, current_time):
            continue
        for key in (
            f"training:{training_date}:True",
            f"training:{training_date}:False",
        ):
            if cache.get(key) is not None:
                cache.delete(key)
        try:
            await ds.get_training(training_date, full=True)
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
        logger.exception("Training detail fetch failed for %s", dt)
        data = {"res": {"trains": []}}
    trains = data.get("res", {}).get("trains", [])
    if not trains:
        trains = data.get("trains", [])
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
    cardio_minutes = 0.0
    cardio_distance = 0.0
    cardio_calories = 0.0
    for train in trains:
        metrics = train.get("_cardio_metrics", {})
        cardio_minutes += float(metrics.get("total_duration_min") or 0)
        cardio_distance += float(metrics.get("distance") or 0)
        cardio_calories += float(metrics.get("calories") or 0)
        if not metrics:
            for movement in train.get("movements", []):
                if movement.get("exetype") != "cardio":
                    continue
                for training_set in movement.get("sets", []):
                    cardio_minutes += float(training_set.get("time") or 0) / 60
                    cardio_distance += float(training_set.get("distance") or 0)
                    cardio_calories += float(training_set.get("calories") or 0)
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
        cardio_summary={
            "minutes": round(cardio_minutes),
            "distance": round(cardio_distance, 2),
            "calories": round(cardio_calories),
        },
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
    freq_stats = compute_frequency_stats(freq_counts, today=today)
    analysis_data = {
        "duration": training_duration_trend(all_trains),
        "calories": calories_trend(all_trains),
        "heart_rate": heart_rate_trend(all_trains),
        "type_dist": training_type_distribution(all_trains_raw, cardio_only=True),
    }

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
        volume_data=vol_data,
        analysis_data=analysis_data,
        freq_dates=freq_dates,
        freq_counts=freq_counts,
        freq_stats=freq_stats,
        movement_names=movement_names,
        movement_options=movement_options,
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
        "estimated_1rm": [h.get("estimated_1rm", 0) for h in hist],
    }
