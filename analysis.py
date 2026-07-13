from typing import Any, Optional, Union

import pandas as pd


def _safe_float(v: Any) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _total_volume_for_movement(movement: dict) -> float:
    total = 0.0
    for s in movement.get("sets", []):
        # Handle superset/descending sets with items
        if "items" in s:
            for item in s["items"]:
                item_set = item.get("set", {})
                w = _safe_float(item_set.get("weight", item_set.get("weight_kg", 0)))
                reps = _safe_float(item_set.get("reps", 0))
                total += w * reps
        else:
            # Check for sub-items inside the set
            sub_items = s.get("items", [])
            if sub_items:
                for item in sub_items:
                    item_set = item.get("set", {})
                    w = _safe_float(item_set.get("weight", item_set.get("weight_kg", 0)))
                    reps = _safe_float(item_set.get("reps", 0))
                    total += w * reps
            else:
                w = _safe_float(s.get("weight", s.get("weight_kg", 0)))
                reps = _safe_float(s.get("reps", 0))
                total += w * reps
    return total


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
    for train in trains:
        seen = set()
        for mov in train.get("movements", []):
            name = mov.get("name", "")
            if name and name not in seen:
                seen.add(name)
                freq[name] = freq.get(name, 0) + 1
    return freq


def summarize_training(trains: list[dict]) -> dict:
    """Produce a summary dict with total volume, total sets, frequency, etc."""
    total_volume = 0.0
    total_sets = 0
    dates = set()

    for train in trains:
        d = train.get("datestr", "")
        if d:
            dates.add(d)
        for mov in train.get("movements", []):
            total_volume += _total_volume_for_movement(mov)
            for s in mov.get("sets", []):
                if "items" in s:
                    total_sets += len(s["items"])
                else:
                    total_sets += 1

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
        date = train.get("datestr", "")
        for mov in train.get("movements", []):
            if mov.get("name", "") != movement_name:
                continue
            max_w = 0.0
            vol = 0.0
            set_count = 0
            for s in mov.get("sets", []):
                w = _safe_float(s.get("weight", s.get("weight_kg", 0)))
                reps = _safe_float(s.get("reps", 0))
                if w > max_w:
                    max_w = w
                vol += w * reps
                set_count += 1
            if date:
                records[date] = {
                    "date": date,
                    "max_weight": max_w,
                    "total_volume": vol,
                    "total_sets": set_count,
                }
    return sorted(records.values(), key=lambda x: x["date"])


def training_frequency_by_date(trains: list[dict]) -> list[str]:
    """Return sorted list of dates that have training records."""
    dates: set[str] = set()
    for train in trains:
        d = train.get("datestr", "")
        if d:
            dates.add(d)
    return sorted(dates)


# ── Training analysis ──


def training_duration_trend(trains: list[dict]) -> list[dict]:
    """Extract training duration in minutes for each training session."""
    records = []
    for train in trains:
        start = train.get("start")
        end = train.get("end")
        if start and end:
            duration_min = round((end - start) / 60000, 1)
            records.append({
                "date": train.get("datestr", ""),
                "duration_min": duration_min,
            })
    records.sort(key=lambda x: x["date"])
    return records


def calories_trend(trains: list[dict]) -> list[dict]:
    """Extract calorie burn for each training session from sets or _cardio_metrics."""
    records = []
    for train in trains:
        total_cal = 0
        # 先从 sets 里加
        for mov in train.get("movements", []):
            for s in mov.get("sets", []):
                cal = _safe_float(s.get("calories", 0))
                if cal > 0:
                    total_cal += cal
                else:
                    metrics = s.get("metrics", {})
                    if isinstance(metrics, dict):
                        total_cal += _safe_float(metrics.get("calories", 0))
        # 再从合并后的 _cardio_metrics 加
        cm = train.get("_cardio_metrics", {})
        if cm.get("calories"):
            total_cal += _safe_float(cm["calories"])
        if total_cal > 0:
            records.append({
                "date": train.get("datestr", ""),
                "calories": total_cal,
            })
    records.sort(key=lambda x: x["date"])
    return records


def heart_rate_trend(trains: list[dict]) -> list[dict]:
    """Extract heart rate data for each training session from sets or _cardio_metrics."""
    records = []
    for train in trains:
        avg_hr = None
        max_hr = None
        for mov in train.get("movements", []):
            for s in mov.get("sets", []):
                ahr = s.get("avgHeartRate")
                mhr = s.get("maxHeartRate")
                if ahr is not None:
                    avg_hr = max(avg_hr or 0, _safe_float(ahr))
                if mhr is not None:
                    max_hr = max(max_hr or 0, _safe_float(mhr))
                metrics = s.get("metrics", {})
                if isinstance(metrics, dict):
                    ahr = metrics.get("avgHeartRate")
                    mhr = metrics.get("maxHeartRate")
                    if ahr is not None:
                        avg_hr = max(avg_hr or 0, _safe_float(ahr))
                    if mhr is not None:
                        max_hr = max(max_hr or 0, _safe_float(mhr))
        # 检查合并后的 _cardio_metrics
        cm = train.get("_cardio_metrics", {})
        if cm.get("avgHeartRate") is not None:
            avg_hr = max(avg_hr or 0, _safe_float(cm["avgHeartRate"]))
        if cm.get("maxHeartRate") is not None:
            max_hr = max(max_hr or 0, _safe_float(cm["maxHeartRate"]))
        if avg_hr is not None or max_hr is not None:
            records.append({
                "date": train.get("datestr", ""),
                "avg_hr": avg_hr,
                "max_hr": max_hr,
            })
    records.sort(key=lambda x: x["date"])
    return records


def _sum_calories(sets: list[dict]) -> float:
    total = 0.0
    for s in sets:
        try:
            c = s.get("calories") or (s.get("metrics", {}) or {}).get("calories")
            if c:
                total += float(c)
        except (ValueError, TypeError):
            pass
    return total


def training_type_distribution(trains: list[dict], cardio_only: bool = False) -> list[dict]:
    """Count frequency and total duration per training type."""
    freq: dict[str, int] = {}
    duration: dict[str, float] = {}
    calories: dict[str, float] = {}
    for train in trains:
        seen = set()
        train_dur = (train.get("end", 0) - train.get("start", 0)) / 60000
        mov_count = len(train.get("movements", []))
        dur_per_mov = train_dur / mov_count if mov_count > 0 else 0
        train_cal = sum(_sum_calories(m.get("sets", [])) for m in train.get("movements", []))
        cm = train.get("_cardio_metrics", {})
        if cm.get("calories"):
            train_cal += float(cm["calories"])
        cal_per_mov = train_cal / mov_count if mov_count > 0 else 0
        for mov in train.get("movements", []):
            name = mov.get("name", "")
            if cardio_only and mov.get("exetype") != "cardio":
                continue
            if name:
                if name not in seen:
                    seen.add(name)
                    freq[name] = freq.get(name, 0) + 1
                duration[name] = duration.get(name, 0) + dur_per_mov
                if cal_per_mov > 0:
                    calories[name] = calories.get(name, 0) + cal_per_mov
    result = []
    for k, v in freq.items():
        item = {"name": k, "count": v, "duration_min": round(duration.get(k, 0), 0)}
        if k in calories:
            item["calories"] = round(calories[k], 0)
        result.append(item)
    return sorted(result, key=lambda x: -x["count"])


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
    """Delta per record vs previous same-type record. Key: 'type:date'."""
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
            else:
                changes[key] = 0.0
            prev[t] = val
    return changes


def calculate_bmi(weight_kg: Optional[float], height_cm: float = 170.0) -> Optional[float]:
    """BMI = weight(kg) / (height(m))². Default height 170cm."""
    if weight_kg is None:
        return None
    height_m = height_cm / 100.0
    return round(weight_kg / (height_m * height_m), 1)
