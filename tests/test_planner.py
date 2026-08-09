# tests/test_planner.py
from datetime import date

from planner import DEFAULT_PLAN_PREFERENCES, generate_plan, _classify_movement


def test_classify_push():
    """推类动作正确归类"""
    assert _classify_movement("卧推") == "push"
    assert _classify_movement("哑铃飞鸟") == "push"
    assert _classify_movement("侧平举") == "push"


def test_classify_pull():
    assert _classify_movement("划船") == "pull"
    assert _classify_movement("哑铃弯举") == "pull"


def test_classify_legs():
    assert _classify_movement("深蹲") == "legs"
    assert _classify_movement("腿举") == "legs"


def test_classify_pull_rdl():
    """哑铃直腿硬拉 classified as pull (analysis.py 分类为 拉力)"""
    assert _classify_movement("哑铃直腿硬拉") == "pull"


def test_classify_core():
    assert _classify_movement("俄罗斯转体") == "core"
    assert _classify_movement("平躺曲腿旋转") == "core"


def test_classify_cardio():
    assert _classify_movement("步行") == "cardio"
    assert _classify_movement("跑步") == "cardio"


def test_classify_unknown():
    assert _classify_movement("XYZ不存在") == "other"


def test_deficit_empty_trains():
    result = generate_plan([])
    assert result["focus_group"]["name"] == "全身"
    assert len(result["suggested_movements"]) == 0


def test_deficit_single_group():
    """只有推类训练 → 其他三组都缺"""
    trains = [{
        "datestr": "2026-07-10",
        "movements": [{"name": "卧推", "sets": [{"done": True}, {"done": True}]}]
    }]
    result = generate_plan(trains, reference_date=date(2026, 7, 12))
    groups = {g["key"]: g for g in result["groups"]}
    assert groups["push"]["count"] == 1
    assert groups["legs"]["deficit"] > groups["push"]["deficit"]


def test_plan_uses_real_reference_date_and_returns_focus_movements():
    trains = [
        {
            "datestr": "2026-08-01",
            "movements": [
                {"name": "俄罗斯转体", "sets": [{"done": True}]},
                {"name": "平躺曲腿旋转", "sets": [{"done": True}]},
            ],
        },
        {
            "datestr": "2026-08-08",
            "movements": [{"name": "卧推", "sets": [{"done": True}]}],
        },
    ]

    result = generate_plan(trains, reference_date=date(2026, 8, 9))

    groups = {g["key"]: g for g in result["groups"]}
    assert groups["core"]["days_since"] == 8
    assert groups["push"]["days_since"] == 1
    assert result["focus_group"]["key"] == "core"
    assert {m["name"] for m in result["suggested_movements"]} == {
        "俄罗斯转体",
        "平躺曲腿旋转",
    }


def test_plan_counts_training_days_separately_from_exercises():
    trains = [{
        "datestr": "2026-08-08",
        "movements": [
            {"name": "卧推", "sets": [{}, {}, {}]},
            {"name": "哑铃飞鸟", "sets": [{}, {}]},
        ],
    }]

    result = generate_plan(trains, reference_date=date(2026, 8, 9))
    push = next(g for g in result["groups"] if g["key"] == "push")

    assert push["count"] == 1
    assert push["exercise_count"] == 2
    assert push["sets"] == 5


def test_plan_builds_seven_day_schedule_from_preferences():
    preferences = {**DEFAULT_PLAN_PREFERENCES, "days_per_week": 3}
    result = generate_plan(
        [{
            "datestr": "2026-08-08",
            "movements": [{"name": "卧推", "sets": [{}, {}]}],
        }],
        reference_date=date(2026, 8, 9),
        preferences=preferences,
    )

    assert len(result["week_plan"]) == 7
    assert sum(day["kind"] == "train" for day in result["week_plan"]) == 3
    assert result["preferences"]["session_minutes"] == 45


def test_low_recovery_makes_today_a_rest_day():
    result = generate_plan(
        [{
            "datestr": "2026-08-08",
            "movements": [{"name": "卧推", "sets": [{}, {}]}],
        }],
        reference_date=date(2026, 8, 9),
        preferences={"days_per_week": 3},
        recovery={"sleep_hours": 4.5, "hrv": 20, "hrv_avg": 40},
    )

    assert result["recommendation"]["action"] == "rest"
    assert result["week_plan"][0]["kind"] == "rest"
    assert sum(day["kind"] == "train" for day in result["week_plan"]) == 3
