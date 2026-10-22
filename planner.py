"""Training plan recommendation engine — pure computation, no I/O."""

from typing import Any, Optional
from datetime import date, timedelta
from analysis import classify_bodypart, iter_movement_sets, movement_is_unfinished


DEFAULT_PLAN_PREFERENCES = {
    "goal": "保持健康",
    "days_per_week": 4,
    "session_minutes": 45,
    "equipment": "哑铃、自重",
    "injuries": "",
}

GROUP_TARGETS = {
    "push": {"sets_30d": 32, "rest_days": 2},
    "pull": {"sets_30d": 32, "rest_days": 2},
    "legs": {"sets_30d": 32, "rest_days": 3},
    "core": {"sets_30d": 20, "rest_days": 1},
    "cardio": {"sets_30d": 12, "rest_days": 1},
}

DEFAULT_MOVEMENTS = {
    "push": ["哑铃卧推", "哑铃推肩", "侧平举"],
    "pull": ["哑铃划船", "哑铃弯举", "哑铃耸肩"],
    "legs": ["哑铃酒杯深蹲", "哑铃直腿硬拉", "哑铃箭步蹲"],
    "core": ["死虫", "俄罗斯转体", "负重仰卧起坐"],
    "cardio": ["快走", "骑行", "慢跑"],
}


def _classify_movement(name: str) -> str:
    """Map movement name to muscle group using classify_bodypart."""
    cat = classify_bodypart(name)
    mapping = {"推力": "push", "胸部": "push", "肩部": "push", "手臂": "push",
               "拉力": "pull", "背部": "pull",
               "腿部": "legs", "臀部": "legs",
               "腹部": "core", "核心": "core",
               "有氧": "cardio"}
    if cat in mapping:
        return mapping[cat]
    # Fallback keyword matching
    push_kw = {"推", "飞鸟", "臂屈伸", "俯卧撑", "前平举", "侧平举", "三头"}
    pull_kw = {"划船", "引体", "下拉", "面拉", "弯举", "二头", "耸肩", "直腿硬拉"}
    legs_kw = {"深蹲", "腿举", "腿屈伸", "腿弯举", "弓步", "臀推", "提踵", "哈克"}
    core_kw = {"卷腹", "平板", "仰卧起坐", "举腿", "转体", "登山者", "熊爬", "平躺曲腿旋转"}
    cardio_kw = {"步行", "跑步", "骑行", "游泳", "椭圆机", "HIIT", "跳绳", "有氧", "行走"}
    for kw_set, group in [(push_kw, "push"), (pull_kw, "pull"), (legs_kw, "legs"),
                          (core_kw, "core"), (cardio_kw, "cardio")]:
        if any(k in name for k in kw_set):
            return group
    return "other"


def normalize_plan_preferences(preferences: Optional[dict], *, strict: bool = False) -> dict:
    result = dict(DEFAULT_PLAN_PREFERENCES)
    if isinstance(preferences, dict):
        result.update({k: v for k, v in preferences.items() if k in result})
    for key, lower, upper in (("days_per_week", 1, 7), ("session_minutes", 15, 180)):
        try:
            if isinstance(result[key], bool):
                raise ValueError("Boolean is not a training count")
            result[key] = min(upper, max(lower, int(result[key])))
        except (TypeError, ValueError, OverflowError) as exc:
            if strict:
                raise ValueError("Invalid numeric preference") from exc
            result[key] = DEFAULT_PLAN_PREFERENCES[key]
    for key in ("goal", "equipment", "injuries"):
        if not isinstance(result[key], str):
            if strict:
                raise ValueError("Invalid text preference")
            result[key] = DEFAULT_PLAN_PREFERENCES[key]
    return result


def _assess_recovery(recovery: Optional[dict]) -> dict:
    """Turn available Apple Health context into a cautious readiness summary."""
    recovery = recovery or {}
    score = 100
    reasons = []
    sleep = recovery.get("sleep_hours")
    if sleep is not None:
        if sleep < 5.5:
            score -= 35
            reasons.append("睡眠明显不足")
        elif sleep < 7:
            score -= 15
            reasons.append("睡眠略少")
        else:
            reasons.append("睡眠充足")

    hrv = recovery.get("hrv")
    hrv_avg = recovery.get("hrv_avg")
    if hrv and hrv_avg:
        ratio = hrv / hrv_avg
        if ratio < 0.8:
            score -= 30
            reasons.append("HRV 低于30日基线")
        elif ratio < 0.95:
            score -= 12
            reasons.append("HRV 略低于基线")
        else:
            reasons.append("HRV 接近或高于基线")

    rhr = recovery.get("rhr")
    rhr_avg = recovery.get("rhr_avg")
    if sleep is None and not (hrv and hrv_avg) and not (rhr and rhr_avg):
        return {
            "score": None,
            "status": "数据不足",
            "intensity": "按训练间隔安排",
            "action": "train",
            "reasons": ["暂无足够的当日恢复数据，按训练间隔安排"],
        }
    if rhr and rhr_avg:
        delta = rhr - rhr_avg
        if delta >= 8:
            score -= 25
            reasons.append("静息心率明显偏高")
        elif delta >= 4:
            score -= 10
            reasons.append("静息心率略高")
        else:
            reasons.append("静息心率未高于基线")

    score = max(0, min(100, score))
    if score < 50:
        status, intensity, action = "需要恢复", "休息或低强度", "rest"
    elif score < 75:
        status, intensity, action = "恢复一般", "中低强度", "train"
    else:
        status, intensity, action = "恢复良好", "正常强度", "train"
    return {
        "score": score,
        "status": status,
        "intensity": intensity,
        "action": action,
        "reasons": reasons,
    }


def _training_day_indexes(days_per_week: int) -> set:
    if days_per_week == 1:
        return {0}
    return {round(i * 6 / (days_per_week - 1)) for i in range(days_per_week)}


def generate_plan(
    trains: list[dict],
    reference_date: Optional[date] = None,
    preferences: Optional[dict] = None,
    recovery: Optional[dict] = None,
) -> dict[str, Any]:
    """Generate an explainable training plan from the last 30 days."""
    reference_date = reference_date or date.today()
    preferences = normalize_plan_preferences(preferences)
    readiness = _assess_recovery(recovery)
    if not trains:
        return {
            "groups": [],
            "focus_group": {"key": "full_body", "name": "全身", "deficit": 0},
            "suggested_movements": [],
            "week_plan": [],
            "preferences": preferences,
            "readiness": readiness,
            "recommendation": {
                "action": readiness["action"],
                "intensity": readiness["intensity"],
                "reason": "暂无训练记录",
            },
        }

    groups = {
        "push": {"name": "推力", "count": 0, "exercise_count": 0, "sets": 0, "last_date": "", "days_since": 0, "_dates": set()},
        "pull": {"name": "拉力", "count": 0, "exercise_count": 0, "sets": 0, "last_date": "", "days_since": 0, "_dates": set()},
        "legs": {"name": "腿部", "count": 0, "exercise_count": 0, "sets": 0, "last_date": "", "days_since": 0, "_dates": set()},
        "core": {"name": "核心", "count": 0, "exercise_count": 0, "sets": 0, "last_date": "", "days_since": 0, "_dates": set()},
        "cardio": {"name": "有氧", "count": 0, "exercise_count": 0, "sets": 0, "last_date": "", "days_since": 0, "_dates": set()},
    }

    mov_tracker: dict[str, dict] = {}
    for t in trains:
        d = t.get("datestr", "")
        for m in t.get("movements", []):
            if movement_is_unfinished(m):
                continue
            name = m.get("name", "")
            if not name:
                continue
            g = "cardio" if m.get("exetype") == "cardio" else _classify_movement(name)
            if g == "other":
                continue
            groups[g]["exercise_count"] += 1
            if d:
                groups[g]["_dates"].add(d)
            if d > groups[g]["last_date"]:
                groups[g]["last_date"] = d
            groups[g]["sets"] += sum(1 for _ in iter_movement_sets(m))

            if name not in mov_tracker:
                mov_tracker[name] = {"group": g, "last_date": d, "count": 0}
            if d > mov_tracker[name]["last_date"]:
                mov_tracker[name]["last_date"] = d
            mov_tracker[name]["count"] += 1

    for key, g in groups.items():
        g["count"] = len(g["_dates"])
        if g["last_date"]:
            g["days_since"] = max(
                0, (reference_date - date.fromisoformat(g["last_date"])).days
            )
        else:
            g["days_since"] = None
        target = GROUP_TARGETS[key]["sets_30d"]
        completed = g["exercise_count"] if key == "cardio" else g["sets"]
        coverage = min(completed / target, 1)
        # Missing history retains scheduling pressure without inventing a date.
        recency_pressure = min(g["days_since"] / 10, 1) if g["days_since"] is not None else 1
        g["target_sets"] = target
        g["coverage_pct"] = round(coverage * 100)
        g["deficit"] = round((1 - coverage) * 70 + recency_pressure * 30, 1)

    active_groups = [g for g in groups.values() if g["count"] > 0]
    focus_pool = active_groups if len(active_groups) >= 2 else list(groups.values())
    focus = max(focus_pool, key=lambda x: x["deficit"])
    focus_key = next(k for k, v in groups.items() if v is focus)

    suggested = []
    for name, info in sorted(mov_tracker.items(), key=lambda x: -x[1]["count"]):
        if info["group"] == focus_key:
            last = info["last_date"]
            if last:
                days_ago = max(0, (reference_date - date.fromisoformat(last)).days)
            else:
                days_ago = 99
            suggested.append({
                "name": name, "group": focus["name"],
                "last_date": last, "days_ago": days_ago,
            })
    suggested = sorted(suggested, key=lambda x: -x["days_ago"])[:5]
    if not suggested:
        suggested = [
            {"name": name, "group": focus["name"], "last_date": "", "days_ago": None}
            for name in DEFAULT_MOVEMENTS[focus_key]
        ]

    ordered_keys = [
        key for key, _ in sorted(groups.items(), key=lambda item: -item[1]["deficit"])
        if key != "cardio"
    ]
    train_indexes = _training_day_indexes(preferences["days_per_week"])
    if readiness["action"] == "rest" and 0 in train_indexes:
        train_indexes.remove(0)
        replacement = next(
            (index for index in range(1, 7) if index not in train_indexes), None
        )
        if replacement is not None:
            train_indexes.add(replacement)
    week_plan = []
    train_number = 0
    weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
    for offset in range(7):
        day = reference_date + timedelta(days=offset)
        is_train = offset in train_indexes
        if is_train:
            key = ordered_keys[train_number % len(ordered_keys)]
            train_number += 1
            week_plan.append({
                "date": day.isoformat(),
                "weekday": "周" + weekday_names[day.weekday()],
                "kind": "train",
                "group_key": key,
                "group": groups[key]["name"],
                "minutes": preferences["session_minutes"],
                "intensity": readiness["intensity"] if offset == 0 else "正常强度",
            })
        else:
            week_plan.append({
                "date": day.isoformat(),
                "weekday": "周" + weekday_names[day.weekday()],
                "kind": "rest",
                "group_key": "rest",
                "group": "恢复/轻活动",
                "minutes": 0,
                "intensity": "休息",
            })

    completed_label = (f"{focus['exercise_count']}条记录" if focus_key == "cardio"
                       else f"{focus['sets']}组")
    recency_label = (f"距上次{focus['days_since']}天" if focus["days_since"] is not None
                     else "上次日期未确认")
    recommendation = {
        "action": readiness["action"],
        "intensity": readiness["intensity"],
        "reason": (
            "恢复指标偏弱，优先休息或低强度活动"
            if readiness["action"] == "rest"
            else f"{focus['name']}近30天完成{completed_label}，{recency_label}"
        ),
    }

    public_groups = []
    for key, group in groups.items():
        public_groups.append({"key": key, **{k: v for k, v in group.items() if k != "_dates"}})

    return {
        "groups": public_groups,
        "focus_group": {"key": focus_key, **{k: v for k, v in focus.items() if k != "_dates"}},
        "suggested_movements": suggested,
        "week_plan": week_plan,
        "preferences": preferences,
        "readiness": readiness,
        "recommendation": recommendation,
    }
