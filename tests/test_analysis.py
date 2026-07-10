import pytest
import pandas as pd
from analysis import (
    summarize_training,
    summarize_diet,
    summarize_diet_detailed,
    get_latest_weight,
    summarize_body,
    training_volume_by_date,
    movement_frequency,
    body_latest,
)


def test_training_volume_by_date_empty():
    result = training_volume_by_date([])
    assert result == {}


def test_training_volume_by_date_basic():
    trains = [
        {
            "datestr": "2026-04-02",
            "movements": [
                {
                    "name": "杠铃卧推",
                    "sets": [
                        {"weight": "60", "reps": "10"},
                        {"weight": "60", "reps": "8"},
                    ],
                }
            ],
        }
    ]
    result = training_volume_by_date(trains)
    assert "2026-04-02" in result
    assert result["2026-04-02"] == 60 * 10 + 60 * 8


def test_movement_frequency():
    trains = [
        {
            "datestr": "2026-04-02",
            "movements": [{"name": "杠铃卧推"}, {"name": "深蹲"}],
        },
        {
            "datestr": "2026-04-04",
            "movements": [{"name": "杠铃卧推"}, {"name": "哑铃飞鸟"}],
        },
    ]
    result = movement_frequency(trains)
    assert result.get("杠铃卧推") == 2
    assert result.get("深蹲") == 1
    assert result.get("哑铃飞鸟") == 1


def test_summarize_body_empty():
    result = summarize_body([])
    assert result["weight_trend"] == []
    assert result["bodyfat_trend"] == []


def test_summarize_body_basic():
    records = [
        {"datestr": "2026-06-01", "type": "weight", "value": 75.0},
        {"datestr": "2026-06-07", "type": "weight", "value": 74.5},
        {"datestr": "2026-06-01", "type": "bodyfat", "value": 18.5},
    ]
    result = summarize_body(records)
    assert len(result["weight_trend"]) == 2
    assert result["weight_trend"][0]["value"] == 75.0
    assert len(result["bodyfat_trend"]) == 1


def test_body_latest():
    records = [
        {"datestr": "2026-06-01", "type": "weight", "value": 75.0},
        {"datestr": "2026-06-07", "type": "weight", "value": 74.5},
        {"datestr": "2026-06-01", "type": "bodyfat", "value": 18.5},
    ]
    latest = body_latest(records)
    assert latest["weight"]["value"] == 74.5
    assert latest["bodyfat"]["value"] == 18.5


def test_summarize_diet_empty():
    result = summarize_diet([])
    assert result["daily_calories"] == {}


def test_summarize_diet_basic():
    records = [
        {
            "date": "2026-06-12",
            "meal_type": "lunch",
            "ntr": {"cal": 500, "protein": 30, "fat": 15, "carb": 50},
        },
        {
            "date": "2026-06-12",
            "meal_type": "dinner",
            "ntr": {"cal": 700, "protein": 40, "fat": 20, "carb": 60},
        },
    ]
    result = summarize_diet(records)
    assert "2026-06-12" in result["daily_calories"]
    assert result["daily_calories"]["2026-06-12"] == 1200
    assert result["macro_split"]["protein"] == 70.0


def test_summarize_training_empty():
    result = summarize_training([])
    assert result["total_volume"] == 0
    assert result["total_sets"] == 0
    assert result["total_days"] == 0


def test_summarize_training_basic():
    trains = [
        {
            "datestr": "2026-04-02",
            "movements": [
                {
                    "name": "杠铃卧推",
                    "sets": [
                        {"weight": "60", "reps": "10"},
                        {"weight": "60", "reps": "8"},
                    ],
                }
            ],
        },
        {
            "datestr": "2026-04-04",
            "movements": [
                {
                    "name": "深蹲",
                    "sets": [
                        {"weight": "80", "reps": "5"},
                    ],
                }
            ],
        },
    ]
    result = summarize_training(trains)
    assert result["total_days"] == 2
    assert result["total_sets"] == 3
    assert result["total_volume"] == (60 * 10 + 60 * 8) + (80 * 5)
    assert "volume_by_date" in result
    assert "movement_frequency" in result
    assert "top_movements" in result


# ── Bodypart and movement analysis ──


def test_classify_bodypart():
    from analysis import classify_bodypart
    assert classify_bodypart("杠铃卧推") == "推力"
    assert classify_bodypart("引体向上") == "拉力"
    assert classify_bodypart("杠铃深蹲") == "腿部"
    assert classify_bodypart("卷腹") == "核心"
    assert classify_bodypart("跑步_有氧训练") == "有氧"
    assert classify_bodypart("未知动作") == "其他"


def test_bodypart_distribution():
    from analysis import bodypart_distribution
    trains = [
        {"datestr": "2026-04-02", "movements": [{"name": "杠铃卧推"}]},
        {"datestr": "2026-04-04", "movements": [{"name": "杠铃深蹲"}]},
        {"datestr": "2026-04-04", "movements": [{"name": "杠铃划船"}]},
    ]
    dist = bodypart_distribution(trains)
    assert dist.get("推力") == 1
    assert dist.get("腿部") == 1
    assert dist.get("拉力") == 1


def test_movement_history():
    from analysis import movement_history
    trains = [
        {
            "datestr": "2026-04-02",
            "movements": [{"name": "杠铃卧推", "sets": [{"weight": "60", "reps": "10"}]}],
        },
        {
            "datestr": "2026-04-04",
            "movements": [{"name": "杠铃卧推", "sets": [{"weight": "65", "reps": "8"}]}],
        },
    ]
    hist = movement_history(trains, "杠铃卧推")
    assert len(hist) == 2
    assert hist[0]["max_weight"] == 60
    assert hist[1]["max_weight"] == 65


def test_training_frequency_by_date():
    from analysis import training_frequency_by_date
    trains = [
        {"datestr": "2026-04-02", "movements": [{"name": "杠铃卧推"}]},
        {"datestr": "2026-04-04", "movements": [{"name": "深蹲"}]},
    ]
    dates = training_frequency_by_date(trains)
    assert dates == ["2026-04-02", "2026-04-04"]


# ── Training analysis ──


def test_duration_trend():
    from analysis import training_duration_trend
    assert training_duration_trend([]) == []
    trains = [{"datestr": "2026-07-08", "start": 1000000, "end": 3700000}]
    result = training_duration_trend(trains)
    assert len(result) == 1
    assert result[0]["duration_min"] == 45.0


def test_calories_trend():
    from analysis import calories_trend
    assert calories_trend([]) == []
    trains = [{"datestr": "2026-07-08", "movements": [
        {"sets": [{"calories": "300"}]}
    ]}]
    result = calories_trend(trains)
    assert len(result) == 1
    assert result[0]["calories"] == 300


def test_heart_rate_trend():
    from analysis import heart_rate_trend
    assert heart_rate_trend([]) == []
    trains = [{"datestr": "2026-07-08", "movements": [
        {"sets": [{"avgHeartRate": "145", "maxHeartRate": "172"}]}
    ]}]
    result = heart_rate_trend(trains)
    assert len(result) == 1
    assert result[0]["avg_hr"] == 145
    assert result[0]["max_hr"] == 172


def test_training_type_distribution_cardio_only():
    from analysis import training_type_distribution
    trains = [
        {"datestr": "2026-07-08", "movements": [{"name": "步行", "exetype": "cardio"}]},
        {"datestr": "2026-07-09", "movements": [{"name": "哑铃卧推", "exetype": "weight"}]},
    ]
    result = training_type_distribution(trains, cardio_only=True)
    assert len(result) == 1
    assert result[0]["name"] == "步行"


def test_training_type_distribution():
    from analysis import training_type_distribution
    assert training_type_distribution([]) == []
    trains = [
        {"datestr": "2026-07-08", "start": 1000000, "end": 3700000, "movements": [{"name": "步行"}]},
        {"datestr": "2026-07-09", "start": 5000000, "end": 7700000, "movements": [{"name": "步行"}]},
        {"datestr": "2026-07-09", "start": 8000000, "end": 10400000, "movements": [{"name": "力量训练"}]},
    ]
    result = training_type_distribution(trains)
    assert len(result) == 2
    assert result[0]["name"] == "步行"
    assert result[0]["count"] == 2
    assert result[0]["duration_min"] > 0


def test_summarize_diet_detailed():
    records = [
        {"date": "2026-07-01", "meal_type": "breakfast", "name": "鸡蛋", "amount": 100, "unit": "g", "ntr": {"cal": 165, "protein": 31, "fat": 3.6, "carb": 0}},
        {"date": "2026-07-01", "meal_type": "lunch", "name": "米饭", "amount": 200, "unit": "g", "ntr": {"cal": 260, "protein": 5, "fat": 1, "carb": 60}},
        {"date": "2026-07-02", "meal_type": "breakfast", "name": "牛奶", "amount": 250, "unit": "g", "ntr": {"cal": 125, "protein": 8, "fat": 5, "carb": 10}},
    ]
    result = summarize_diet_detailed(records)

    assert "daily_calories" in result
    assert result["daily_calories"]["2026-07-01"] == 425.0
    assert result["daily_calories"]["2026-07-02"] == 125.0

    assert "daily_macros" in result
    d1 = result["daily_macros"]["2026-07-01"]
    assert d1["protein"] == 36.0
    assert d1["carbs"] == 60.0
    assert d1["fat"] == pytest.approx(4.6)

    assert "meal_breakdown" in result
    assert len(result["meal_breakdown"]["2026-07-01"]["breakfast"]) == 1
    assert result["meal_breakdown"]["2026-07-01"]["breakfast"][0]["name"] == "鸡蛋"

    assert "total_ratio" in result
    r = result["total_ratio"]
    total_pct = r["protein_pct"] + r["carbs_pct"] + r["fat_pct"]
    assert abs(total_pct - 100) < 1

    assert "summary" in result
    assert result["summary"]["days_with_data"] == 2


def test_get_latest_weight_empty():
    assert get_latest_weight([]) is None


def test_get_latest_weight_basic():
    records = [
        {"datestr": "2026-06-01", "type": "weight", "value": 75.0},
        {"datestr": "2026-06-07", "type": "weight", "value": 74.5},
        {"datestr": "2026-06-01", "type": "bodyfat", "value": 18.5},
    ]
    assert get_latest_weight(records) == 74.5


def test_get_latest_weight_only_bodyfat():
    records = [
        {"datestr": "2026-06-01", "type": "bodyfat", "value": 18.5},
        {"datestr": "2026-06-07", "type": "bodyfat", "value": 18.0},
    ]
    assert get_latest_weight(records) is None
