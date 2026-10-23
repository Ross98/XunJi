"""Sleep aggregation must retain timezones, fractions and uncertainty."""
import pytest
from sleep_stats import parse_intervals, summarize_intervals, confirmed_sleep_minutes


def row(start, end, stage='AsleepCore'):
    return dict(type=stage, start_date=start, end_date=end)


def summary(*records):
    return summarize_intervals(parse_intervals(records))


def test_sleep_duration_uses_absolute_time_across_offset_change():
    result, = summary(row('2026-10-22 01:00:00 +0200', '2026-10-22 03:00:00 +0100'))
    assert result['AsleepCore'] == 180


def test_same_instants_with_different_offsets_are_not_double_counted():
    result, = summary(row('2026-10-22 01:00:00 +0000', '2026-10-22 03:00:00 +0000'),
                      row('2026-10-22 09:00:00 +0800', '2026-10-22 11:00:00 +0800'))
    assert result['AsleepCore'] == 120
    assert result['record_count'] == 2


def test_disjoint_absolute_intervals_do_not_create_false_awake_conflict():
    result, = summary(row('2026-10-22 01:00:00 +0000', '2026-10-22 03:00:00 +0000'),
                      row('2026-10-22 02:00:00 +0800', '2026-10-22 03:00:00 +0800', 'Awake'))
    assert result.get('Conflict', 0) == 0
    assert confirmed_sleep_minutes(result) == 120


def test_completion_day_remains_source_local_day():
    result, = summary(row('2026-10-22 00:00:00 +0800', '2026-10-22 01:00:00 +0800'))
    assert result['date'] == '2026-10-22'


def test_subsecond_conflict_cannot_round_into_confirmed_sleep():
    result, = summary(row('2026-10-22T01:00:00Z', '2026-10-22T02:00:00Z'),
                      row('2026-10-22T01:30:00.1Z', '2026-10-22T01:30:00.2Z', 'Awake'))
    assert result['has_conflict'] is True
    assert confirmed_sleep_minutes(result) is None


@pytest.mark.parametrize('start,end', [
    ('2026-10-22', '2026-10-23'),
    ('2026-10-22 01:00:00 nonsense', '2026-10-22 02:00:00 nonsense'),
    ('2026-10-22 01:00:00 +0000', '2026-10-22 02:00:00 +0200'),
    ('2026-10-22 01:00:00', '2026-10-22 02:00:00 +0800'),
])
def test_invalid_interval_is_not_treated_as_valid_sleep(start, end):
    assert summary(row(start, end)) == []


def test_mixed_clock_domains_do_not_publish_confirmed_total():
    result, = summary(row('2026-10-22 01:00:00 +0800', '2026-10-22 03:00:00 +0800'),
                      row('2026-10-22 01:00:00', '2026-10-22 03:00:00'))
    assert result['time_unconfirmed'] is True
    assert confirmed_sleep_minutes(result) is None


def test_naive_legacy_only_clock_remains_supported():
    result, = summary(row('2026-10-22 01:00:00', '2026-10-22 03:00:00'))
    assert confirmed_sleep_minutes(result) == 120


def test_iso_sleep_ending_on_requested_day_is_not_lost_by_sql_clock_format(isolated_health_db):
    ah = isolated_health_db
    with ah._conn() as conn:
        conn.execute('INSERT INTO health_sleep(type,start_date,end_date) VALUES(?,?,?)',
                     ('AsleepCore', '2026-10-22T01:00:00Z', '2026-10-22T03:00:00Z'))
    result, = ah.get_sleep_summary('2026-10-22', '2026-10-22')
    assert result['AsleepCore'] == 120


def test_submicrosecond_awake_conflict_remains_unconfirmed():
    result, = summary(row('2026-10-22T01:00:00Z', '2026-10-22T02:00:00Z'),
                      row('2026-10-22T01:30:00.0000001Z', '2026-10-22T01:30:00.0000002Z', 'Awake'))
    assert result['has_conflict']
    assert confirmed_sleep_minutes(result) is None


def test_same_end_instant_with_different_completion_dates_is_unconfirmed():
    result, = summary(row('2026-10-21T23:00:00Z', '2026-10-22T00:00:00Z'),
                      row('2026-10-21T22:00:00-0100', '2026-10-21T23:00:00-0100'))
    assert result['time_unconfirmed']
    assert confirmed_sleep_minutes(result) is None


def test_window_expands_connected_history_but_not_disconnected_padding(isolated_health_db, monkeypatch):
    from datetime import datetime, timedelta
    import sleep_stats
    ah = isolated_health_db
    base = datetime(2026, 1, 1)
    with ah._conn() as conn:
        for index in range(200):
            start = base + timedelta(days=index)
            end = start + timedelta(hours=1)
            conn.execute('INSERT INTO health_sleep(type,start_date,end_date) VALUES(?,?,?)',
                         ('AsleepCore', start.isoformat()+'Z', end.isoformat()+'Z'))
    parsed_counts = []
    original = sleep_stats.parse_intervals
    def observe(records):
        parsed = original(records)
        parsed_counts.append(len(parsed))
        return parsed
    monkeypatch.setattr(sleep_stats, 'parse_intervals', observe)
    result, = ah.get_sleep_summary('2026-04-10', '2026-04-10')
    assert result['AsleepCore'] == 60
    assert len(parsed_counts) <= 2
    assert sum(parsed_counts) < 30


@pytest.mark.parametrize('requested,expected', [('2026-10-21', []), ('2026-10-25', [7200])])
def test_long_connected_component_is_not_truncated_at_padding(isolated_health_db, requested, expected):
    from datetime import datetime, timedelta
    ah = isolated_health_db
    base = datetime(2026, 10, 20)
    with ah._conn() as conn:
        for index in range(5):
            start = base + timedelta(days=index)
            end = start + timedelta(days=1)
            conn.execute('INSERT INTO health_sleep(type,start_date,end_date) VALUES(?,?,?)',
                         ('AsleepCore', start.isoformat()+'Z', end.isoformat()+'Z'))
    assert [entry['AsleepCore'] for entry in ah.get_sleep_summary(requested, requested)] == expected


@pytest.mark.parametrize('kind', ['offset_change', 'tiny_conflict', 'mixed_clocks'])
def test_sleep_time_status_agrees_across_pages(isolated_health_db, tmp_cache, tmp_path, monkeypatch, kind):
    import asyncio
    from datetime import date
    import httpx
    from app import app
    from routes import dashboard, health, plan
    ah = isolated_health_db
    today = date.today().isoformat()
    if kind == 'offset_change':
        records = [row(today+' 01:00:00 +0200', today+' 03:00:00 +0100')]
    elif kind == 'tiny_conflict':
        records = [row(today+'T01:00:00Z', today+'T03:00:00Z'),
                   row(today+'T02:00:00.1Z', today+'T02:00:00.2Z', 'Awake')]
    else:
        records = [row(today+' 01:00:00 +0800', today+' 03:00:00 +0800'),
                   row(today+' 01:00:00', today+' 03:00:00')]
    with ah._conn() as conn:
        conn.executemany('INSERT INTO health_sleep(type,start_date,end_date) VALUES(:type,:start_date,:end_date)', records)
    trains = [{'datestr': today, 'movements': [{'name': '卧推', 'sets': [{}]}]}]
    tmp_cache.set(f'training:{today}:True', {'trains': trains})
    class Service:
        async def get_training(self, day, full=False):
            return {'trains': trains if day == today else []}
        async def get_body(self, *args, **kwargs):
            return {'records': []}
    for module in (dashboard, health, plan):
        monkeypatch.setattr(module, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(dashboard, 'get_data_service', Service)
    monkeypatch.setattr(dashboard, '_ah', ah)
    monkeypatch.setattr(plan, 'PLAN_CONFIG_PATH', tmp_path/'plan.json')
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            responses = [await client.get(path) for path in ('/dashboard/content', '/health', '/plan')]
            assert all(response.status_code == 200 for response in responses)
            html = [response.text for response in responses]
        if kind == 'offset_change':
            assert '>3.0</strong><span>小时' in html[0]
            assert '>3.0小时</strong>' in html[1]
            assert '>3.0h</b>' in html[2]
        elif kind == 'tiny_conflict':
            assert '来源冲突，完整时长未确认' in html[0]
            assert '不足0.01' in html[1]
            assert '睡眠来源冲突，未用于恢复评分' in html[2]
        else:
            assert '记录时区未确认，完整时长未确认' in html[0]
            assert '睡眠记录时区未确认，本次完整时长未确认' in html[1]
            assert '睡眠记录时区未确认，未用于恢复评分' in html[2]
    asyncio.run(run())


@pytest.mark.parametrize('seed', range(5))
def test_offset_sweep_agrees_with_independent_minute_oracle(seed):
    import random
    from datetime import datetime, timedelta, timezone
    rng = random.Random(seed)
    base = datetime(2026, 10, 22, 10, tzinfo=timezone.utc)
    source = []
    minute_stages = [set() for _ in range(90)]
    stages = ['AsleepCore', 'AsleepDeep', 'AsleepREM', 'AsleepUnspecified', 'Awake', 'InBed']
    for _ in range(40):
        begin = rng.randrange(60)
        end = begin + rng.randrange(1, 15)
        stage = rng.choice(stages)
        zone = timezone(timedelta(hours=rng.choice([-5, 0, 2, 8])))
        source.append(row((base+timedelta(minutes=begin)).astimezone(zone).isoformat(),
                          (base+timedelta(minutes=end)).astimezone(zone).isoformat(), stage))
        for minute in range(begin, end):
            minute_stages[minute].add(stage)
    expected = {}
    def add(key):
        expected[key] = expected.get(key, 0) + 1
    for active in minute_stages:
        asleep = active & set(stages[:4])
        if 'InBed' in active:
            add('InBed')
        if 'Awake' in active and asleep:
            add('Conflict')
        elif 'Awake' in active:
            add('Awake')
        elif asleep:
            detailed = asleep - {'AsleepUnspecified'}
            if len(detailed) == 1:
                add(next(iter(detailed)))
            else:
                add('AsleepUnspecified')
                if len(detailed) > 1:
                    add('StageConflict')
    actual = summary(*source)
    for stage in stages + ['Conflict', 'StageConflict']:
        assert sum(day.get(stage, 0) for day in actual) == expected.get(stage, 0)


@pytest.mark.parametrize('day', ['0001-01-01', '9999-12-31'])
def test_sleep_range_padding_does_not_overflow_calendar(isolated_health_db, day):
    ah = isolated_health_db
    with ah._conn() as conn:
        conn.execute('INSERT INTO health_sleep(type,start_date,end_date) VALUES(?,?,?)',
                     ('AsleepCore', day+'T01:00:00+08:00', day+'T02:00:00+08:00'))
    result, = ah.get_sleep_summary(day, day)
    assert result['AsleepCore'] == 60
