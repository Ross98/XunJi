from typing import Any


def generate_plan(recent_trains: list[dict]) -> dict[str, Any]:
    """Generate a simple training plan suggestion based on recent history."""
    movements_seen: dict[str, int] = {}
    last_date = ""

    for train in recent_trains:
        if train.get("datestr", "") > last_date:
            last_date = train["datestr"]
        for mov in train.get("movements", []):
            name = mov.get("name", "")
            if name:
                movements_seen[name] = movements_seen.get(name, 0) + 1

    sorted_movements = sorted(movements_seen.items(), key=lambda x: x[1])

    return {
        "based_on_last_date": last_date,
        "total_recent_days": len(recent_trains),
        "movements_used": len(sorted_movements),
        "suggested_focus": _suggest_focus(sorted_movements),
        "suggested_movements": [m[0] for m in sorted_movements[:6]],
    }


def _suggest_focus(sorted_movements: list[tuple[str, int]]) -> str:
    push = {"卧推", "推胸", "飞鸟", "臂屈伸", "俯卧撑", "推举", "前平举", "侧平举"}
    pull = {"划船", "引体向上", "高位下拉", "面拉", "弯举", "硬拉"}
    legs = {"深蹲", "腿举", "腿屈伸", "腿弯举", "弓步", "臀推", "罗马尼亚硬拉"}

    push_count = sum(1 for m, _ in sorted_movements if any(p in m for p in push))
    pull_count = sum(1 for m, _ in sorted_movements if any(p in m for p in pull))
    legs_count = sum(1 for m, _ in sorted_movements if any(p in m for p in legs))

    least = min((push_count, "推力"), (pull_count, "拉力"), (legs_count, "腿部"), key=lambda x: x[0])
    if least[0] == 0:
        return f"建议优先训练{least[1]}，近期未涉及"
    return "训练分布较均匀，可继续均衡发展"
