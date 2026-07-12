"""Training plan recommendation engine — pure computation, no I/O."""

from typing import Any
from datetime import date
from analysis import classify_bodypart


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


def generate_plan(trains: list[dict]) -> dict[str, Any]:
    """Generate training deficit analysis from recent training records."""
    if not trains:
        return {
            "groups": [],
            "focus_group": {"key": "full_body", "name": "全身", "deficit": 0},
            "suggested_movements": [],
        }

    groups = {
        "push": {"name": "推力", "count": 0, "sets": 0, "last_date": "", "days_since": 0},
        "pull": {"name": "拉力", "count": 0, "sets": 0, "last_date": "", "days_since": 0},
        "legs": {"name": "腿部", "count": 0, "sets": 0, "last_date": "", "days_since": 0},
        "core": {"name": "核心", "count": 0, "sets": 0, "last_date": "", "days_since": 0},
        "cardio": {"name": "有氧", "count": 0, "sets": 0, "last_date": "", "days_since": 0},
    }

    mov_tracker: dict[str, dict] = {}
    today = max(t.get("datestr", "") for t in trains) if trains else ""

    for t in trains:
        d = t.get("datestr", "")
        for m in t.get("movements", []):
            name = m.get("name", "")
            if not name:
                continue
            g = _classify_movement(name)
            if g == "other":
                continue
            groups[g]["count"] += 1
            if d > groups[g]["last_date"]:
                groups[g]["last_date"] = d
            for s in m.get("sets", []):
                if isinstance(s, dict):
                    if "items" in s:
                        groups[g]["sets"] += len(s["items"])
                    else:
                        groups[g]["sets"] += 1

            if name not in mov_tracker:
                mov_tracker[name] = {"group": g, "last_date": d, "count": 0}
            if d > mov_tracker[name]["last_date"]:
                mov_tracker[name]["last_date"] = d
            mov_tracker[name]["count"] += 1

    max_count = max(g["count"] for g in groups.values()) or 1
    for g in groups.values():
        if g["last_date"]:
            g["days_since"] = (date.fromisoformat(today) - date.fromisoformat(g["last_date"])).days
        else:
            g["days_since"] = 99
        freq_ratio = g["count"] / max_count
        g["deficit"] = round((1 - freq_ratio) * g["days_since"] + (1 - freq_ratio) * 20, 1)

    sorted_groups = sorted(groups.values(), key=lambda x: -x["deficit"])
    focus = sorted_groups[0]
    focus_key = next(k for k, v in groups.items() if v is focus)

    suggested = []
    for name, info in sorted(mov_tracker.items(), key=lambda x: -x[1]["count"]):
        if info["group"] == focus["name"]:
            last = info["last_date"]
            if last:
                days_ago = (date.fromisoformat(today) - date.fromisoformat(last)).days
            else:
                days_ago = 99
            if days_ago >= 3:
                suggested.append({
                    "name": name, "group": focus["name"],
                    "last_date": last, "days_ago": days_ago,
                })
    suggested = sorted(suggested, key=lambda x: -x["days_ago"])[:5]

    return {
        "groups": [{"key": k, **v} for k, v in groups.items()],
        "focus_group": {"key": focus_key, **focus},
        "suggested_movements": suggested,
    }
