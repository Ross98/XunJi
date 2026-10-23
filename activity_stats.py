"""Read-only cumulative samples; never infer missing source priority."""

from collections import Counter, defaultdict
from datetime import date, timezone
import heapq
import math
from health_values import health_interval_keys, health_mean


def daily_activity_totals(records):
    days = defaultdict(list)
    valid_days, invalid_days = set(), set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get('start_date'), str):
            continue
        day = record['start_date'][:10]
        if day in invalid_days:
            continue
        if day not in valid_days:
            try:
                if date.fromisoformat(day).isoformat() != day:
                    raise ValueError('Noncanonical activity date')
            except ValueError:
                invalid_days.add(day)
                continue
            valid_days.add(day)
        days[day].append(record)
    result = {}
    for day, samples in days.items():
        values = []
        invalid = False
        intervals = []
        clock_modes = set()
        for sample in samples:
            try:
                value = float(sample['value'])
                if isinstance(sample['value'], bool) or not math.isfinite(value) or value < 0:
                    raise ValueError('Invalid cumulative sample')
                values.append(value)
            except (KeyError, TypeError, ValueError, OverflowError):
                invalid = True
            try:
                if len(sample['start_date']) < 19 or len(sample['end_date']) < 19:
                    raise ValueError('Cumulative intervals need time-of-day evidence')
                start, end = health_interval_keys(sample['start_date'], sample['end_date'], require_timezone=False)
                clock_modes.add(start[0].tzinfo is None)
                if start[0].tzinfo is not None:
                    # A shared UTC tzinfo keeps sorting comparisons cheap while
                    # preserving the exact fractional tail in the second key.
                    start = (start[0].astimezone(timezone.utc), start[1])
                    end = (end[0].astimezone(timezone.utc), end[1])
                if end > start:
                    intervals.append((start, end, str(sample.get('source_name') or '')))
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                invalid = True
        # Naive legacy clocks can be compared to each other, but mixing them
        # with offsets would invent a timezone. Keep that day's total unknown.
        if len(clock_modes) > 1:
            invalid = True
            intervals = []
        active = []
        sources = Counter()
        overlap = False
        for start, end, source in sorted(intervals):
            while active and active[0][0] <= start:
                _, previous_source = heapq.heappop(active)
                sources[previous_source] -= 1
            if len(active) > sources[source]:
                overlap = True
                break
            heapq.heappush(active, (end, source))
            sources[source] += 1
        try:
            total = math.fsum(values)
        except OverflowError:
            total = None
        result[day] = {'value': total if not (invalid or overlap) else None,
                       'overlap': overlap}
    return result


def confirmed_activity_average(records, start, end):
    values = [item['value'] for day, item in daily_activity_totals(records).items()
              if start <= day <= end and item['value'] is not None]
    mean = health_mean(values)
    return (round(mean) if mean is not None else None, len(values))
