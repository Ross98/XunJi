"""Read-only sleep aggregation, preserving absolute times and local completion dates."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from health_values import health_interval_keys

ASLEEP_STAGES = frozenset({'AsleepCore', 'AsleepDeep', 'AsleepREM', 'AsleepUnspecified'})
STAGES = ASLEEP_STAGES | {'InBed', 'Awake'}


@dataclass(frozen=True)
class SleepInterval:
    start: tuple
    end: tuple
    stage: str

    @property
    def aware(self):
        return self.start[0].tzinfo is not None

    @property
    def completion_day(self):
        return self.end[0].date().isoformat()


def parse_intervals(records):
    intervals = []
    for record in records:
        try:
            stage = record['type'].removeprefix('HKCategoryValueSleepAnalysis')
            raw_start, raw_end = record['start_date'], record['end_date']
            if not all(isinstance(value, str) and len(value) >= 19 for value in (raw_start, raw_end)):
                continue
            start, end = health_interval_keys(raw_start, raw_end, require_timezone=False)
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            continue
        if stage in STAGES and end > start:
            intervals.append(SleepInterval(start, end, stage))
    # Unknown local clocks cannot be compared to absolute instants.
    return sorted(intervals, key=lambda interval: (interval.aware, interval.start, interval.end, interval.stage))


def connected_components(intervals):
    component = []
    boundary = None
    for interval in intervals:
        if component and (interval.aware != component[0].aware or interval.start > boundary):
            yield component, boundary
            component = []
        boundary = max(boundary, interval.end) if component else interval.end
        component.append(interval)
    if component:
        yield component, boundary


def window_components(intervals, start, end):
    """Keep only components that can touch the requested local completion days.

    A source offset is strictly less than 24h. Extra fetched, disconnected
    components must not repeatedly expand the SQL window into unrelated years.
    """
    lower = date.fromisoformat(start[:10]).toordinal() * 86400 if start else None
    upper = (date.fromisoformat(end[:10]).toordinal() + 1) * 86400 if end else None

    def seconds(key):
        dt = key[0]
        offset = dt.utcoffset()
        return (dt.toordinal() * 86400 + dt.hour * 3600 + dt.minute * 60 + dt.second
                + dt.microsecond / 1_000_000 - (offset.total_seconds() if offset else 0))

    selected = []
    for component, boundary in connected_components(intervals):
        margin = 86400 if component[0].aware else 0
        if lower is not None and seconds(boundary) < lower - margin:
            continue
        if upper is not None and seconds(component[0].start) > upper + margin:
            continue
        selected.extend(component)
    return selected


def summarize_intervals(intervals):
    """Archive connected intervals by completion day; never infer across a gap."""
    by_day = defaultdict(lambda: defaultdict(float))
    record_counts = Counter()
    clock_domains = defaultdict(set)
    uncertain_days = set()

    def finish(component, end):
        events = defaultdict(Counter)
        for interval in component:
            events[interval.start][interval.stage] += 1
            events[interval.end][interval.stage] -= 1
        active = Counter()
        previous = None
        completion_days = {interval.completion_day for interval in component if interval.end == end}
        day = max(completion_days)
        if len(completion_days) > 1:
            uncertain_days.add(day)
        clock_domains[day].add(component[0].aware)
        record_counts[day] += len(component)
        totals = by_day[day]
        for instant, updates in sorted(events.items()):
            if previous is not None:
                seconds = ((instant[0] - previous[0]).total_seconds()
                           + float(instant[1] - previous[1]) / 1_000_000)
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

    for component, boundary in connected_components(intervals):
        finish(component, boundary)
    return [dict(date=day, record_count=record_counts[day],
                 has_conflict=totals.get('Conflict', 0) > 0,
                 has_stage_conflict=totals.get('StageConflict', 0) > 0,
                 time_unconfirmed=day in uncertain_days or len(clock_domains[day]) > 1,
                 **{stage: round(seconds / 60, 2) for stage, seconds in totals.items()})
            for day, totals in sorted(by_day.items(), reverse=True)]


def confirmed_sleep_minutes(summary):
    if (not summary or summary.get('has_conflict') or summary.get('time_unconfirmed')
            or summary.get('Conflict', 0) > 0):
        return None
    minutes = sum(summary.get(stage, 0) for stage in ASLEEP_STAGES)
    return minutes if minutes > 0 else None
