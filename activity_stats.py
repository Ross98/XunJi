"""Read-only cumulative samples; never infer missing source priority."""

from collections import Counter, defaultdict
from datetime import datetime
import heapq
import math


def _instant(value):
    value = value.strip().replace('Z', '+00:00').replace(' +', '+').replace(' -', '-')
    if len(value) > 19 and value[-5] in '+-' and value[-4:].isdigit():
        value = value[:-2] + ':' + value[-2:]
    return datetime.fromisoformat(value).timestamp()


def daily_activity_totals(records):
    days = defaultdict(list)
    for record in records:
        if isinstance(record.get('start_date'), str):
            days[record['start_date'][:10]].append(record)
    result = {}
    for day, samples in days.items():
        total = 0.0
        invalid = False
        intervals = []
        for sample in samples:
            try:
                value = float(sample['value'])
                if isinstance(sample['value'], bool) or not math.isfinite(value) or value < 0:
                    raise ValueError('Invalid cumulative sample')
                total += value
            except (KeyError, TypeError, ValueError, OverflowError):
                invalid = True
            try:
                start, end = _instant(sample['start_date']), _instant(sample['end_date'])
                if end > start:
                    intervals.append((start, end, str(sample.get('source_name') or '')))
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                pass
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
        result[day] = {'value': total if not (invalid or overlap) and math.isfinite(total) else None,
                       'overlap': overlap}
    return result


def confirmed_activity_average(records, start, end):
    values = [item['value'] for day, item in daily_activity_totals(records).items()
              if start <= day <= end and item['value'] is not None]
    return (round(sum(values) / len(values)), len(values)) if values else (None, 0)
