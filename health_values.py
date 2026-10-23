"""Health value/time validation and read-only views; missing data never becomes zero."""

from datetime import date, datetime
from decimal import Decimal
import math
import re
from sys import float_info


def _health_timestamp_parts(value, require_timezone):
    if not isinstance(value, str):
        raise ValueError('Health timestamps must be strings')
    text = value.strip().replace('Z', '+00:00').replace(' +', '+').replace(' -', '-')
    if len(text) > 19 and text[-5] in '+-' and text[-4:].isdigit():
        text = text[:-2] + ':' + text[-2:]
    elif len(text) > 21 and text[-7] in '+-' and text[-6:].isdigit():
        text = text[:-4] + ':' + text[-4:-2] + ':' + text[-2:]
    fraction = re.search(r'([T ]\d{2}:\d{2}:\d{2})[.,](\d+)', text) if '.' in text or ',' in text else None
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


def health_interval_keys(start, end, *, require_timezone=True):
    """Return comparable exact instant keys without rounding submicrosecond tails."""
    begin, begin_tail = _health_timestamp_parts(start, require_timezone)
    finish, finish_tail = _health_timestamp_parts(end, require_timezone)
    if (begin.tzinfo is None) != (finish.tzinfo is None):
        raise ValueError('Health interval timezones do not match')
    begin_key = (begin, Decimal('0.' + begin_tail) if begin_tail else 0)
    finish_key = (finish, Decimal('0.' + finish_tail) if finish_tail else 0)
    if finish_key < begin_key:
        raise ValueError('Health interval ends before it starts')
    return begin_key, finish_key


def health_interval(start, end, *, require_timezone=True):
    """Validate original precision; return datetimes for legacy display projections."""
    begin, finish = health_interval_keys(start, end, require_timezone=require_timezone)
    return begin[0], finish[0]


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


def health_weighted_mean(observations):
    """Mean of finite values with known positive weights; preserve unknown weights."""
    numbers = [(health_number(value), health_number(weight)) for value, weight in observations]
    if not numbers or any(value is None or weight is None or weight <= 0 for value, weight in numbers):
        return None
    try:
        products = _normal_products(numbers)
        if products is not None:
            mean = health_number(math.fsum(products) / math.fsum(weight for _, weight in numbers))
            uncertain_zero = mean == 0 and any(value != 0 for value, _ in numbers)
            if (mean is not None and not uncertain_zero
                    and min(value for value, _ in numbers) <= mean <= max(value for value, _ in numbers)):
                return mean
    except (OverflowError, ValueError):
        pass

    # Float inputs are exact binary rationals. Rare overflow/underflow paths
    # need no guessed magnitude cap or arbitrary decimal precision.
    from fractions import Fraction
    numerator = sum((Fraction(value) * Fraction(weight) for value, weight in numbers), Fraction())
    denominator = sum((Fraction(weight) for _, weight in numbers), Fraction())
    return _fraction_value(numerator / denominator)


def _fraction_value(exact):
    try:
        value = float(exact)
    except OverflowError:
        return None
    return None if value == 0 and exact != 0 else health_number(value)


def _normal_products(numbers):
    """Subnormal products need exact arithmetic even when they are nonzero."""
    products = [value * weight for value, weight in numbers]
    try:
        if all(math.isfinite(product) and (abs(product) >= float_info.min or value == 0 or weight == 0)
               for (value, weight), product in zip(numbers, products)):
            return products
    except OverflowError:
        pass
    return None


def finite_sum(values):
    """Propagate unknown terms; recover representable sums after intermediate overflow."""
    numbers = [health_number(value) for value in values]
    if any(value is None for value in numbers):
        return None
    try:
        total = health_number(math.fsum(numbers))
        if total != 0 or not any(numbers):
            return total
    except OverflowError:
        pass
    from fractions import Fraction
    return _fraction_value(sum((Fraction(value) for value in numbers), Fraction()))


def finite_product_sum(observations):
    """Sum finite products without manufacturing zero or infinity on overflow."""
    numbers = [(health_number(value), health_number(weight)) for value, weight in observations]
    if any(value is None or weight is None for value, weight in numbers):
        return None
    products = _normal_products(numbers)
    if products is not None:
        try:
            total = health_number(math.fsum(products))
            if total != 0 or not any(products):
                return total
        except OverflowError:
            pass
    from fractions import Fraction
    exact = sum((Fraction(value) * Fraction(weight) for value, weight in numbers), Fraction())
    return _fraction_value(exact)
