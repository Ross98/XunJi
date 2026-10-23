"""Read-only health measurement views; missing data never becomes zero."""

from datetime import date
import math


def health_number(value, scale=1):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        scaled = value * scale
        return scaled if math.isfinite(scaled) else None
    except OverflowError:
        return None


def metric_records_view(records):
    result = []
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get('start_date'), str):
            continue
        day = row['start_date'][:10]
        try:
            if date.fromisoformat(day).isoformat() != day:
                continue
        except ValueError:
            continue
        result.append(dict(row, value=health_number(row.get('value'))))
    return result


def health_mean(values):
    numbers = [value for raw in values if (value := health_number(raw)) is not None]
    if not numbers:
        return None
    try:
        return health_number(math.fsum(numbers) / len(numbers))
    except OverflowError:
        pass
    mean, count = 0.0, 0
    for value in numbers:
        count += 1
        delta = value - mean
        mean = (mean + delta / count if math.isfinite(delta) else
                mean * (1 - 1 / count) + value / count)
    return health_number(mean) if count else None
