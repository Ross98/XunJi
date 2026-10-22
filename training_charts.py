"""Calendar-based chart windows, independent of browser timezone and sample count."""
from collections import defaultdict
from datetime import date, timedelta

from analysis import (
    calories_trend, heart_rate_trend, training_duration_trend,
    training_type_distribution, training_volume_by_date,
)


def select_training_window(trains, reference: date, days: int):
    start = reference - timedelta(days=days - 1)
    selected = []
    for train in trains:
        try:
            day = date.fromisoformat(train.get("datestr", ""))
        except (TypeError, ValueError):
            continue
        if start <= day <= reference:
            selected.append(train)
    return selected


def build_chart_ranges(merged_trains, raw_trains, reference: date):
    volumes, analysis = {}, {}
    for days in (30, 90):
        selected = select_training_window(merged_trains, reference, days)
        raw = select_training_window(raw_trains, reference, days)
        daily = training_volume_by_date(selected)
        if days == 90:
            weekly = defaultdict(float)
            for day, value in daily.items():
                parsed = date.fromisoformat(day)
                monday = parsed - timedelta(days=parsed.weekday())
                weekly[monday.isoformat()] += value
            points = weekly
        else:
            points = daily
        volumes[str(days)] = [{"date": day, "value": points[day]} for day in sorted(points)]
        analysis[str(days)] = {
            "duration": training_duration_trend(selected),
            "calories": calories_trend(selected),
            "heart_rate": heart_rate_trend(selected),
            "type_dist": training_type_distribution(raw, cardio_only=True),
        }
    return volumes, analysis
