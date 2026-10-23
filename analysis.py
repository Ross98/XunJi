from typing import Any, Optional, Union
import math

def _safe_float(v: Any) -> float:
    if isinstance(v, bool):
        return 0.0
    try:
        value = float(v)
        return value if math.isfinite(value) else 0.0
    except (ValueError, TypeError, OverflowError):
        return 0.0


def iter_movement_sets(movement: dict, *, include_unfinished: bool = False):
    """Yield completed sets; missing legacy completion flags remain compatible."""
    sets = movement.get("sets")
    if not isinstance(sets, list):
        return
    for group in sets:
        if not isinstance(group, dict):
            continue
        if "items" not in group:
            if include_unfinished or group.get("done") is not False:
                yield group
            continue
        items = group["items"]
        if isinstance(items, list):
            for item in items:
                if (isinstance(item, dict) and isinstance(item.get("set"), dict)
                        and (include_unfinished or item["set"].get("done") is not False)):
                    yield item["set"]


def movement_is_unfinished(movement: dict) -> bool:
    """Only explicit unfinished sets establish that an activity was not done."""
    return (next(iter_movement_sets(movement, include_unfinished=True), None) is not None
            and next(iter_movement_sets(movement), None) is None)


def training_is_unfinished(train: dict) -> bool:
    """Exclude only sessions whose movements all explicitly remain unfinished."""
    movements = list(train.get("movements") or [])
    preserved = train.get("_cardio_movements")
    if isinstance(preserved, list):
        movements.extend(preserved)
    return bool(movements) and all(movement_is_unfinished(movement) for movement in movements)


def _total_volume_for_movement(movement: dict) -> float:
    return sum(
        _safe_float(s.get("weight", s.get("weight_kg", 0))) * _safe_float(s.get("reps", 0))
        for s in iter_movement_sets(movement)
    )


def training_volume_by_date(trains: list[dict]) -> dict[str, float]:
    """Calculate total training volume (weight x reps) grouped by date."""
    volumes: dict[str, float] = {}
    for train in trains:
        date = train.get("datestr", "")
        if not date:
            continue
        total = 0.0
        for mov in train.get("movements", []):
            total += _total_volume_for_movement(mov)
        if total > 0:
            volumes[date] = volumes.get(date, 0) + total
    return volumes


def movement_frequency(trains: list[dict]) -> dict[str, int]:
    """Count how many training days each movement appears."""
    freq: dict[str, int] = {}
    seen = set()
    for train in trains:
        day = train.get("datestr", "")
        if not day:
            continue
        for mov in train.get("movements", []):
            name = mov.get("name", "")
            if movement_is_unfinished(mov):
                continue
            if name and (day, name) not in seen:
                seen.add((day, name))
                freq[name] = freq.get(name, 0) + 1
    return freq


def summarize_training(trains: list[dict]) -> dict:
    """Produce a summary dict with total volume, total sets, frequency, etc."""
    total_volume = 0.0
    total_sets = 0
    dates = set()

    for train in trains:
        d = train.get("datestr", "")
        if d and not training_is_unfinished(train):
            dates.add(d)
        for mov in train.get("movements", []):
            total_volume += _total_volume_for_movement(mov)
            total_sets += sum(1 for _ in iter_movement_sets(mov))

    vol_by_date = training_volume_by_date(trains)
    freq = movement_frequency(trains)
    top = sorted(freq.items(), key=lambda x: -x[1])[:10]

    return {
        "total_volume": total_volume,
        "total_sets": total_sets,
        "total_days": len(dates),
        "volume_by_date": vol_by_date,
        "movement_frequency": freq,
        "top_movements": top,
    }


def summarize_diet(records: list[dict]) -> dict:
    """Summarize diet records: daily calories, macro split."""
    daily_calories: dict[str, float] = {}
    total_protein = 0.0
    total_carbs = 0.0
    total_fat = 0.0
    total_cals = 0.0

    for rec in records:
        ntr = rec.get("ntr", {})
        cal = _safe_float(ntr.get("cal", 0))
        protein = _safe_float(ntr.get("protein", 0))
        carbs = _safe_float(ntr.get("carb", 0))
        fat = _safe_float(ntr.get("fat", 0))

        date = rec.get("date", "")
        if date:
            daily_calories[date] = daily_calories.get(date, 0) + cal

        total_protein += protein
        total_carbs += carbs
        total_fat += fat
        total_cals += cal

    count = len(records) if len(records) > 0 else 1
    return {
        "daily_calories": daily_calories,
        "avg_daily_calories": total_cals / count,
        "macro_split": {
            "protein": round(total_protein, 1),
            "carbs": round(total_carbs, 1),
            "fat": round(total_fat, 1),
        },
    }




def _parse_macros(ntr: dict) -> tuple:
    """Return (protein, carbs, fat, cal) from ntr dict."""
    return (
        _safe_float(ntr.get('protein', 0)),
        _safe_float(ntr.get('carb', 0)),
        _safe_float(ntr.get('fat', 0)),
        _safe_float(ntr.get('cal', 0)),
    )


def summarize_diet_detailed(records: list[dict]) -> dict:
    """Summarize diet records with detailed breakdown by date and meal type."""
    daily_calories: dict[str, float] = {}
    daily_macros: dict[str, dict] = {}
    meal_breakdown: dict[str, dict] = {}
    total_protein = 0.0
    total_carbs = 0.0
    total_fat = 0.0
    total_cals = 0.0

    for rec in records:
        date = rec.get('date', '')
        meal_type = rec.get('meal_type', 'other')
        protein, carbs, fat, cal = _parse_macros(rec.get('ntr', {}))

        daily_calories[date] = daily_calories.get(date, 0) + cal

        if date not in daily_macros:
            daily_macros[date] = {'protein': 0.0, 'carbs': 0.0, 'fat': 0.0}
        daily_macros[date]['protein'] += protein
        daily_macros[date]['carbs'] += carbs
        daily_macros[date]['fat'] += fat

        if date not in meal_breakdown:
            meal_breakdown[date] = {}
        if meal_type not in meal_breakdown[date]:
            meal_breakdown[date][meal_type] = []
        meal_breakdown[date][meal_type].append({
            'name': rec.get('name', ''),
            'amount': rec.get('amount', 0),
            'unit': rec.get('unit', 'g'),
            'ntr': rec.get('ntr', {}),
        })

        total_protein += protein
        total_carbs += carbs
        total_fat += fat
        total_cals += cal

    total_energy = total_protein * 4 + total_carbs * 4 + total_fat * 9
    days_with_data = len(daily_calories)

    return {
        'daily_calories': daily_calories,
        'daily_macros': daily_macros,
        'meal_breakdown': meal_breakdown,
        'total_ratio': {
            'protein_pct': round(total_protein * 4 / total_energy * 100, 1) if total_energy else 0,
            'carbs_pct': round(total_carbs * 4 / total_energy * 100, 1) if total_energy else 0,
            'fat_pct': round(total_fat * 9 / total_energy * 100, 1) if total_energy else 0,
        },
        'summary': {
            'avg_calories': round(total_cals / days_with_data, 0) if days_with_data else 0,
            'avg_protein': round(total_protein / days_with_data, 1) if days_with_data else 0,
            'avg_carbs': round(total_carbs / days_with_data, 1) if days_with_data else 0,
            'avg_fat': round(total_fat / days_with_data, 1) if days_with_data else 0,
            'days_with_data': days_with_data,
        },
    }


def summarize_body(records: list[dict]) -> dict:
    """Summarize body records: weight and bodyfat trends."""
    weight = []
    bodyfat = []

    for rec in records:
        entry = {"date": rec.get("datestr", ""), "value": rec.get("value")}
        t = rec.get("type", "")
        if t == "weight":
            weight.append(entry)
        elif t == "bodyfat":
            bodyfat.append(entry)

    weight.sort(key=lambda x: x["date"])
    bodyfat.sort(key=lambda x: x["date"])

    return {
        "weight_trend": weight,
        "bodyfat_trend": bodyfat,
    }


def body_latest(records: list[dict]) -> dict:
    """Get the latest entry for each body metric type. Sort by date, keep newest."""
    sorted_recs = sorted(records, key=lambda x: x.get("datestr", ""))
    latest: dict[str, dict] = {}
    for rec in sorted_recs:
        t = rec.get("type", "")
        if t and rec.get("value") is not None:
            latest[t] = {"value": rec["value"], "date": rec.get("datestr", "")}
    return latest

# ── Bodypart classification ──


def classify_bodypart(name: str) -> str:
    """Classify a movement name into bodypart category."""
    push = {"卧推", "推胸", "飞鸟", "臂屈伸", "俯卧撑", "推举",
            "前平举", "侧平举", "双杠", "三头", "绳索下压", "窄距"}
    pull = {"划船", "引体向上", "下拉", "面拉", "弯举", "硬拉",
            "二头", "绳索面拉", "高位下拉", "对握"}
    legs = {"深蹲", "腿举", "腿屈伸", "腿弯举", "弓步", "臀推",
            "罗马尼亚硬拉", "提踵", "哈克", "臀冲", "驴踢", "内收", "外展"}
    core = {"卷腹", "平板支撑", "仰卧起坐", "举腿", "转体",
            "俄罗斯转体", "登山者", "熊爬"}
    cardio = {"跑步", "步行", "骑行", "划船", "游泳", "椭圆机",
              "有氧", "HIIT", "跳绳", "爬楼梯", "滑雪", "行走"}

    name_lower = name
    for keyword_set, category in [
        (push, "推力"), (pull, "拉力"), (legs, "腿部"),
        (core, "核心"), (cardio, "有氧"),
    ]:
        for kw in keyword_set:
            if kw in name_lower:
                return category
    return "其他"


def bodypart_distribution(trains: list[dict]) -> dict[str, int]:
    """Count training days per bodypart category."""
    distribution: dict[str, int] = {}
    seen: dict[str, set[str]] = {}
    for train in trains:
        date = train.get("datestr", "")
        for mov in train.get("movements", []):
            name = mov.get("name", "")
            if movement_is_unfinished(mov):
                continue
            if not name:
                continue
            bp = classify_bodypart(name)
            if bp not in seen:
                seen[bp] = set()
            if date not in seen[bp]:
                seen[bp].add(date)
                distribution[bp] = distribution.get(bp, 0) + 1
    return distribution


def movement_history(trains: list[dict], movement_name: str) -> list[dict]:
    """Extract weight progression for a specific movement across dates.
    Returns sorted list of {date, max_weight, total_volume, total_sets}.
    """
    records: dict[str, dict] = {}
    for train in trains:
        day = train.get("datestr", "")
        if not day:
            continue
        for mov in train.get("movements", []):
            if mov.get("name", "") != movement_name:
                continue
            if movement_is_unfinished(mov):
                continue
            row = records.setdefault(day, {
                "date": day, "max_weight": 0.0, "total_volume": 0.0,
                "total_sets": 0, "estimated_1rm": 0.0,
            })
            for s in iter_movement_sets(mov):
                w = _safe_float(s.get("weight", s.get("weight_kg", 0)))
                reps = _safe_float(s.get("reps", 0))
                row["max_weight"] = max(row["max_weight"], w)
                row["total_volume"] += w * reps
                row["total_sets"] += 1
                if w > 0 and reps > 0:
                    row["estimated_1rm"] = max(row["estimated_1rm"], w * (1 + reps / 30))
    for row in records.values():
        row["estimated_1rm"] = round(row["estimated_1rm"], 1)
    return sorted(records.values(), key=lambda x: x["date"])


def training_frequency_by_date(trains: list[dict]) -> list[str]:
    """Return sorted list of dates that have training records."""
    from datetime import datetime
    dates: set[str] = set()
    for train in trains:
        if training_is_unfinished(train):
            continue
        d = train.get("datestr", "")
        if d:
            dates.add(d)
            continue
        ts = train.get("start", 0)
        if ts:
            try:
                dt = datetime.fromtimestamp(int(ts) / 1000)
                dates.add(f"{dt.year}-{str(dt.month).zfill(2)}-{str(dt.day).zfill(2)}")
            except (ValueError, OSError):
                pass
    return sorted(dates)


# ── Training analysis ──


def session_bounds(train: dict) -> Optional[tuple[float, float]]:
    """Validated millisecond bounds; missing or malformed times cannot prove overlap."""
    start, end = train.get("start"), train.get("end")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (start, end)):
        return None
    try:
        if end > start and all(math.isfinite(v) for v in (start, end, end - start)):
            return start, end
    except OverflowError:
        pass
    return None


def training_time_coverage(trains: list[dict]) -> dict:
    """Observed clock-time coverage, with overlapping sessions counted once."""
    trains = [train for train in trains if not training_is_unfinished(train)]
    intervals = [bounds for train in trains if (bounds := session_bounds(train)) is not None]
    incomplete = len(intervals) < len(trains)
    if not intervals:
        return {"minutes": None, "incomplete": incomplete}
    intervals.sort()
    start, end = intervals[0]
    total = 0
    for next_start, next_end in intervals[1:]:
        if next_start > end:
            total += end - start
            start, end = next_start, next_end
        else:
            end = max(end, next_end)
    total += end - start
    return {"minutes": total / 60000, "incomplete": incomplete}


def _metric_number(record: dict, *keys: str) -> float:
    nested = record.get("metrics")
    for source in (record, nested if isinstance(nested, dict) else {}):
        for key in keys:
            value = _safe_float(source.get(key))
            if value > 0:
                return value
    return 0.0


def summarize_set_metrics(sets) -> dict:
    """Aggregate independent segment metrics, preserving unknown session averages."""
    result = {"calories": 0.0, "distance": 0.0, "duration_s": 0.0,
              "avgHeartRate": None, "maxHeartRate": None}
    averages = []
    for record in sets:
        duration = _metric_number(record, "time", "workoutTime", "duration_s")
        result["duration_s"] += duration
        result["calories"] += _metric_number(record, "calories")
        result["distance"] += _metric_number(record, "distance")
        avg = _metric_number(record, "avgHeartRate")
        maximum = _metric_number(record, "maxHeartRate")
        if avg:
            averages.append((avg, duration))
        if maximum:
            result["maxHeartRate"] = max(result["maxHeartRate"] or 0, maximum)
    if len(averages) == 1:
        result["avgHeartRate"] = averages[0][0]
    elif averages and all(duration > 0 for _, duration in averages):
        result["avgHeartRate"] = sum(avg * duration for avg, duration in averages) / sum(
            duration for _, duration in averages)
    result["heart_rate_observations"] = len(averages)
    return result


def session_metrics(train: dict, *, cardio_only: bool = False) -> dict:
    movements = list(train.get("movements", []))
    preserved = train.get("_cardio_movements")
    if isinstance(preserved, list):
        movements.extend(preserved)
    sets = [s for mov in movements
            if not cardio_only or mov.get("exetype") == "cardio"
            for s in iter_movement_sets(mov)]
    # Older merged payloads may retain only an aggregate; never count a new
    # aggregate again when its original segments are available.
    if not isinstance(preserved, list) and train.get("_cardio_metrics"):
        sets.append(train["_cardio_metrics"])
    return summarize_set_metrics(sets)


def training_duration_trend(trains: list[dict]) -> list[dict]:
    """Extract observed training duration in minutes, excluding invalid intervals."""
    records = []
    for train in trains:
        if training_is_unfinished(train):
            continue
        bounds = session_bounds(train)
        if bounds:
            records.append({"date": train.get("datestr", ""),
                            "duration_min": round((bounds[1] - bounds[0]) / 60000, 1)})
    return sorted(records, key=lambda x: x["date"])


def calories_trend(trains: list[dict]) -> list[dict]:
    records = []
    for train in trains:
        total = session_metrics(train)["calories"]
        if total > 0:
            records.append({"date": train.get("datestr", ""), "calories": total})
    return sorted(records, key=lambda x: x["date"])


def heart_rate_trend(trains: list[dict]) -> list[dict]:
    records = []
    for train in trains:
        metrics = session_metrics(train)
        if metrics["heart_rate_observations"] or metrics["maxHeartRate"] is not None:
            records.append({"date": train.get("datestr", ""),
                            "avg_hr": metrics["avgHeartRate"], "max_hr": metrics["maxHeartRate"]})
    return sorted(records, key=lambda x: x["date"])


def training_type_distribution(trains: list[dict], cardio_only: bool = False) -> list[dict]:
    """Attribute measured metrics to their type, without dividing unrelated session totals."""
    totals = {}
    for train in trains:
        movements = list(train.get("movements") or [])
        movements.extend(train.get("_cardio_movements") or [])
        movements = [mov for mov in movements
                     if isinstance(mov, dict) and not movement_is_unfinished(mov)]
        per_type = {}
        for mov in movements:
            name = mov.get("name")
            if not isinstance(name, str) or not name or (cardio_only and mov.get("exetype") != "cardio"):
                continue
            sets = list(iter_movement_sets(mov))
            durations = [_metric_number(record, "time", "workoutTime", "duration_s") for record in sets]
            entry = per_type.setdefault(name, {"seconds": 0, "calories": 0, "complete": True})
            entry["seconds"] += sum(durations)
            entry["calories"] += sum(_metric_number(record, "calories") for record in sets)
            entry["complete"] &= bool(sets) and all(value > 0 for value in durations)
        bounds = session_bounds(train)
        for name, entry in per_type.items():
            # Only a single-type session can safely supply otherwise absent
            # duration evidence. A mixed session cannot be divided by guesswork.
            sole_type = all(mov.get("name") == name and
                            (not cardio_only or mov.get("exetype") == "cardio") for mov in movements)
            if not entry["seconds"] and bounds and sole_type:
                entry["seconds"] = (bounds[1] - bounds[0]) / 1000
                entry["complete"] = True
            total = totals.setdefault(name, {"count": 0, "seconds": 0, "calories": 0, "complete": True})
            total["count"] += 1
            total["seconds"] += entry["seconds"]
            total["calories"] += entry["calories"]
            total["complete"] &= entry["complete"]
    result = []
    for name, total in totals.items():
        minutes = round(total["seconds"] / 60, 2)
        item = {"name": name, "count": total["count"],
                "duration_min": minutes if total["complete"] else None}
        if not total["complete"]:
            item["known_duration_min"] = minutes
        if total["calories"] > 0:
            item["calories"] = round(total["calories"], 0)
        result.append(item)
    return sorted(result, key=lambda item: (-item["count"], item["name"]))


def get_latest_weight(records: list[dict]) -> Optional[float]:
    """Extract latest body weight from body records sorted by date."""
    weights: list[tuple[str, float]] = []
    for rec in records:
        if rec.get("type") == "weight":
            val = rec.get("value")
            if val is not None:
                weights.append((rec.get("datestr", ""), _safe_float(val)))
    if not weights:
        return None
    weights.sort(key=lambda x: x[0], reverse=True)
    return weights[0][1]


# ── Body analysis ──


def body_stats(records: list[dict]) -> dict[str, dict[str, float]]:
    """Period stats: min/max/avg for weight and bodyfat."""
    from typing import Any as _Any

    stats: dict[str, dict[str, float]] = {}
    for rec in records:
        t = rec.get("type", "")
        v = rec.get("value")
        if t and v is not None:
            try:
                val = float(v)
            except (ValueError, TypeError):
                continue
            if t not in stats:
                stats[t] = {"min": val, "max": val, "sum": val, "count": 1}
            else:
                s = stats[t]
                s["min"] = min(s["min"], val)
                s["max"] = max(s["max"], val)
                s["sum"] += val
                s["count"] += 1
    result: dict[str, dict[str, float]] = {}
    for t, s in stats.items():
        result[t] = {
            "min": round(s["min"], 2),
            "max": round(s["max"], 2),
            "avg": round(s["sum"] / s["count"], 2),
        }
    return result


def body_changes(records: list[dict]) -> dict[str, float]:
    """Observed deltas only; the first same-type record has no baseline."""
    changes: dict[str, float] = {}
    prev: dict[str, float] = {}
    # records are newest-first; reverse for chronological
    for rec in reversed(records):
        t = rec.get("type", "")
        v = rec.get("value")
        if t and v is not None:
            try:
                val = float(v)
            except (ValueError, TypeError):
                continue
            key = f"{t}:{rec.get('datestr', '')}"
            if t in prev:
                changes[key] = round(val - prev[t], 2)
            prev[t] = val
    return changes


def calculate_bmi(
    weight_kg: Optional[Union[float, str]], height_cm: Union[float, str] = 170.0
) -> Optional[float]:
    """BMI = weight(kg) / (height(m))². Default height 170cm."""
    if weight_kg is None:
        return None
    try:
        weight = float(weight_kg)
        height_m = float(height_cm) / 100.0
    except (TypeError, ValueError):
        return None
    if height_m <= 0:
        return None
    return round(weight / (height_m * height_m), 1)
