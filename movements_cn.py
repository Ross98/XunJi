from __future__ import annotations

"""训记动作名工具

- 中英文对照映射（处理 API 返回的英文类型名）
- 官方标准动作名验证
"""
from pathlib import Path

# ── 英文类型名 → 中文 ──
# API 会返回 training type 的英文名，这些不在官方动作表里

TYPE_CN: dict[str, str] = {
    "TraditionalStrengthTraining": "力量训练",
    "BodyweightTraining": "自重训练",
    "StrengthTraining": "力量训练",
    "FunctionalStrengthTraining": "力量训练",
    "HighIntensityIntervalTraining": "高强度间歇训练",
    "CircuitTraining": "循环训练",
    "CoreTraining": "核心训练",
    "CrossTraining": "交叉训练",
    "Flexibility": "柔韧性训练",
    "Yoga": "瑜伽",
    "Pilates": "普拉提",
    "Stretching": "拉伸",
    "Walking": "步行",
    "Running": "跑步",
    "Cycling": "骑行",
    "Swimming": "游泳",
    "Rowing": "划船",
    "Elliptical": "椭圆机",
    "StairClimbing": "爬楼梯",
    "Cardio": "有氧训练",
    "AppleHealthWorkout": "苹果健康训练",
    "MixedCardio": "混合有氧",
    # 常见动作英文名（API 特殊场景回退用）
    "BarbellBenchPress": "杠铃卧推",
    "BenchPress": "卧推",
    "DumbbellBenchPress": "哑铃卧推",
    "Squat": "深蹲",
    "BarbellSquat": "杠铃深蹲",
    "Deadlift": "硬拉",
    "RomanianDeadlift": "罗马尼亚硬拉",
    "PullUp": "引体向上",
    "ChinUp": "反握引体向上",
    "BarbellRow": "杠铃划船",
    "DumbbellRow": "哑铃划船",
    "OverheadPress": "推举",
    "ShoulderPress": "肩推",
    "LateralRaise": "侧平举",
    "BicepCurl": "弯举",
    "TricepsPushdown": "臂屈伸",
    "LegPress": "腿举",
    "LegExtension": "腿屈伸",
    "LegCurl": "腿弯举",
    "Lunges": "弓步",
    "HipThrust": "臀推",
    "CalfRaise": "提踵",
    "Plank": "平板支撑",
    "PushUp": "俯卧撑",
    "Dip": "臂屈伸",
}

# ── 加载官方标准动作名表 ──
_OFFICIAL_MOVEMENTS: set[str] | None = None


def _load_official_movements() -> set[str]:
    """从 docs/Xunji-movements.md 解析官方标准动作名。"""
    path = Path(__file__).parent / "docs" / "Xunji-movements.md"
    if not path.exists():
        return set()
    names: set[str] = set()
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        # 从 Markdown 表格行提取中文名：| N | 动作中文名 |
        if line.startswith("|") and "|" in line[1:]:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            # 跳过表头和分隔行
            if len(parts) >= 2 and parts[0].isdigit():
                names.add(parts[1])
    return names


def is_official_movement(name: str) -> bool:
    """检查动作名是否在官方标准动作表中。"""
    global _OFFICIAL_MOVEMENTS
    if _OFFICIAL_MOVEMENTS is None:
        _OFFICIAL_MOVEMENTS = _load_official_movements()
    return name in _OFFICIAL_MOVEMENTS


def movement_name_cn(name: str) -> str:
    """将动作名/类型名转为中文。"""
    if not name:
        return name
    # 已经是中文 → 不动
    if any("\u4e00" <= c <= "\u9fff" for c in name):
        return name
    # 查映射表
    return TYPE_CN.get(name, name)


GENERIC_TYPES = {"AppleHealthWorkout", "TraditionalStrengthTraining", "FunctionalStrengthTraining"}


def apply_movement_cn_to_trains(trains: list[dict]) -> list[dict]:
    """替换训练数据中所有 movement name — 中文映射 + 用训练标题替代泛型名。"""
    result = []
    for train in trains:
        t = dict(train)
        title = t.get("title", "")
        if "movements" in t:
            updated = []
            for m in t["movements"]:
                name = m.get("name", "")
                if name in GENERIC_TYPES and title:
                    name = title
                else:
                    name = movement_name_cn(name)
                updated.append({**m, "name": name})
            t["movements"] = updated
        result.append(t)
    return result
