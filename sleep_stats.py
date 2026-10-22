"""Read-only sleep interval aggregation for the dashboard's local-clock records."""
from collections import Counter, defaultdict
from datetime import datetime

ASLEEP_STAGES = frozenset({'AsleepCore', 'AsleepDeep', 'AsleepREM', 'AsleepUnspecified'})
STAGES = ASLEEP_STAGES | {'InBed', 'Awake'}


def parse_intervals(records):
    intervals = []
    for record in records:
        try:
            stage = record['type'].removeprefix('HKCategoryValueSleepAnalysis')
            start = datetime.fromisoformat(record['start_date'][:19])
            end = datetime.fromisoformat(record['end_date'][:19])
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if stage in STAGES and end > start:
            intervals.append((start, end, stage))
    return sorted(intervals)


def summarize_intervals(intervals):
    """Archive connected intervals by completion day; never infer across a gap."""
    by_day = defaultdict(lambda: defaultdict(float))
    record_counts = Counter()

    def finish(component, end):
        events = defaultdict(Counter)
        for begin, finish_at, stage in component:
            events[begin][stage] += 1
            events[finish_at][stage] -= 1
        active = Counter()
        previous = None
        day = end.date().isoformat()
        record_counts[day] += len(component)
        totals = by_day[day]
        for instant, updates in sorted(events.items()):
            if previous is not None:
                seconds = (instant - previous).total_seconds()
                stages = {stage for stage, count in active.items() if count > 0}
                asleep = stages & ASLEEP_STAGES
                if 'InBed' in stages:
                    totals['InBed'] += seconds
                if 'Awake' in stages and asleep:
                    totals['Conflict'] += seconds
                elif 'Awake' in stages:
                    totals['Awake'] += seconds
                elif asleep:
                    detailed = asleep - {'AsleepUnspecified'}
                    if len(detailed) == 1:
                        totals[next(iter(detailed))] += seconds
                    else:
                        totals['AsleepUnspecified'] += seconds
                        if len(detailed) > 1:
                            totals['StageConflict'] += seconds
            active.update(updates)
            previous = instant

    component = []
    boundary = None
    for interval in intervals:
        start, end, _ = interval
        if boundary is not None and start > boundary:
            finish(component, boundary)
            component = []
        boundary = max(boundary, end) if component else end
        component.append(interval)
    if component:
        finish(component, boundary)
    return [dict(date=day, record_count=record_counts[day], **{stage: round(seconds / 60, 2) for stage, seconds in totals.items()})
            for day, totals in sorted(by_day.items(), reverse=True)]


def confirmed_sleep_minutes(summary):
    if not summary or summary.get('Conflict', 0) > 0:
        return None
    minutes = sum(summary.get(stage, 0) for stage in ASLEEP_STAGES)
    return minutes if minutes > 0 else None
