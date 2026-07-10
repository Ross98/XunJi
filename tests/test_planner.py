import pytest
from planner import generate_plan, _suggest_focus


def test_generate_plan_empty():
    result = generate_plan([])
    assert result["total_recent_days"] == 0
    assert result["suggested_movements"] == []


def test_generate_plan_basic():
    trains = [
        {"datestr": "2026-04-02", "movements": [{"name": "杠铃卧推"}, {"name": "哑铃飞鸟"}]},
        {"datestr": "2026-04-04", "movements": [{"name": "深蹲"}, {"name": "腿举"}]},
    ]
    result = generate_plan(trains)
    assert result["total_recent_days"] == 2
    assert result["movements_used"] == 4
    assert "哑铃飞鸟" in result["suggested_movements"]


def test_suggest_focus():
    result = _suggest_focus([("杠铃卧推", 3), ("哑铃飞鸟", 2)])
    assert "拉力" in result or "腿部" in result
