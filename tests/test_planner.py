# tests/test_planner.py
from planner import generate_plan, _classify_movement


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
    result = generate_plan(trains)
    groups = {g["key"]: g for g in result["groups"]}
    assert groups["push"]["count"] == 1
    assert groups["legs"]["deficit"] > groups["push"]["deficit"]
