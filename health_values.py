"""Health value/time validation and read-only views; missing data never becomes zero."""

from datetime import date, datetime
import math
import re


def _health_timestamp_parts(value, require_timezone):
    if not isinstance(value, str):
        raise ValueError('Health timestamps must be strings')
    text = value.strip().replace('Z', '+00:00').replace(' +', '+').replace(' -', '-')
    text = re.sub(r'([+-])(\d{2})(\d{2})(\d{2})?$',
                  lambda m: m[1] + m[2] + ':' + m[3] + (':' + m[4] if m[4] else ''), text)
    fraction = re.search(r'([T ]\d{2}:\d{2}:\d{2})[.,](\d+)', text)
    tail = ''
    if fraction:
        digits = fraction[2]
        tail = digits[6:]
        # Older Python accepts only millisecond/microsecond fractions. Keep the
        # discarded digits separately so validation never rounds an inversion away.
        text = (text[:fraction.start()] + fraction[1] + '.' + digits[:6].ljust(6, '0')
                + text[fraction.end():])
    parsed = datetime.fromisoformat(text)
    if require_timezone and parsed.tzinfo is None:
        raise ValueError('Health timestamp requires a timezone')
    return parsed, tail


def health_timestamp(value):
    """Parse an aware XML/ISO instant for second-resolution display projection."""
    return _health_timestamp_parts(value, True)[0]


def health_interval(start, end, *, require_timezone=True):
    """Validate full-precision instants before a projection truncates timestamps."""
    begin, begin_tail = _health_timestamp_parts(start, require_timezone)
    finish, finish_tail = _health_timestamp_parts(end, require_timezone)
    if (begin.tzinfo is None) != (finish.tzinfo is None):
        raise ValueError('Health interval timezones do not match')
    precision = max(len(begin_tail), len(finish_tail))
    if (finish < begin or
            (finish == begin and finish_tail.ljust(precision, '0') < begin_tail.ljust(precision, '0'))):
        raise ValueError('Health interval ends before it starts')
    return begin, finish


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
