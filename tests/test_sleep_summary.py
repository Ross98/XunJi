import pytest


def insert(ah, stage, start, end, source='watch'):
    with ah._conn() as conn:
        conn.execute('INSERT INTO health_sleep (type,start_date,end_date,source_name) VALUES (?,?,?,?)',
                     ('HKCategoryValueSleepAnalysis' + stage, start + ' +0800', end + ' +0800', source))


def test_midnight_stages_belong_to_one_completion_day(isolated_health_db):
    ah = isolated_health_db
    insert(ah, 'AsleepCore', '2026-10-21 22:00:00', '2026-10-21 23:00:00')
    insert(ah, 'AsleepDeep', '2026-10-21 23:00:00', '2026-10-22 02:00:00')
    insert(ah, 'AsleepCore', '2026-10-22 02:00:00', '2026-10-22 06:00:00')
    rows = ah.get_sleep_summary('2026-10-22', '2026-10-22')
    assert len(rows) == 1
    assert rows[0]['date'] == '2026-10-22'
    assert rows[0]['AsleepCore'] == 300
    assert rows[0]['AsleepDeep'] == 180
    assert ah.get_sleep_summary('2026-10-21', '2026-10-21') == []


def test_short_stages_are_rounded_after_summing(isolated_health_db):
    ah = isolated_health_db
    insert(ah, 'AsleepCore', '2026-10-22 01:00:00', '2026-10-22 01:00:36')
    insert(ah, 'AsleepCore', '2026-10-22 01:00:36', '2026-10-22 01:01:12')
    assert ah.get_sleep_summary()[0]['AsleepCore'] == 1.2


def test_duplicate_and_coarse_sleep_do_not_double_count(isolated_health_db):
    ah = isolated_health_db
    insert(ah, 'AsleepUnspecified', '2026-10-21 22:00:00', '2026-10-22 06:00:00', 'phone')
    insert(ah, 'AsleepCore', '2026-10-21 23:00:00', '2026-10-22 03:00:00')
    insert(ah, 'AsleepCore', '2026-10-22 00:00:00', '2026-10-22 04:00:00', 'other')
    row, = ah.get_sleep_summary()
    assert row['AsleepCore'] == 300
    assert row['AsleepUnspecified'] == 180


def test_conflicting_awake_and_asleep_are_reported_as_unknown(isolated_health_db):
    ah = isolated_health_db
    insert(ah, 'AsleepCore', '2026-10-22 01:00:00', '2026-10-22 03:00:00')
    insert(ah, 'Awake', '2026-10-22 02:00:00', '2026-10-22 02:30:00', 'other')
    row, = ah.get_sleep_summary()
    assert row['Conflict'] == 30
    assert row['AsleepCore'] == 90
    assert row.get('Awake', 0) == 0


def test_conflicting_sleep_stages_preserve_sleep_but_not_a_guessed_stage(isolated_health_db):
    ah = isolated_health_db
    insert(ah, 'AsleepCore', '2026-10-22 01:00:00', '2026-10-22 03:00:00')
    insert(ah, 'AsleepDeep', '2026-10-22 02:00:00', '2026-10-22 02:30:00', 'other')
    row, = ah.get_sleep_summary()
    assert row['AsleepCore'] == 90
    assert row['AsleepUnspecified'] == 30
    assert row['StageConflict'] == 30


def test_separate_sleep_days_stay_separate(isolated_health_db):
    ah = isolated_health_db
    insert(ah, 'AsleepCore', '2026-10-20 22:00:00', '2026-10-21 06:00:00')
    insert(ah, 'AsleepCore', '2026-10-21 22:00:00', '2026-10-22 06:00:00')
    rows = ah.get_sleep_summary('2026-10-21', '2026-10-22')
    assert [row['date'] for row in rows] == ['2026-10-22', '2026-10-21']
    assert all(row['AsleepCore'] == 480 for row in rows)


@pytest.mark.parametrize('conflict', [False, True])
def test_sleep_totals_agree_across_all_three_pages(
    isolated_health_db, tmp_cache, tmp_path, monkeypatch, conflict
):
    import asyncio
    from datetime import date, timedelta
    import httpx
    from app import app
    from routes import dashboard, health, plan
    ah = isolated_health_db
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    insert(ah, 'AsleepCore', yesterday + ' 22:00:00', yesterday + ' 23:00:00')
    insert(ah, 'AsleepDeep', yesterday + ' 23:00:00', today + ' 02:00:00')
    insert(ah, 'AsleepCore', today + ' 02:00:00', today + ' 06:00:00')
    if conflict:
        insert(ah, 'Awake', today + ' 03:00:00', today + ' 04:00:00', 'other')
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
    monkeypatch.setattr(plan, 'PLAN_CONFIG_PATH', tmp_path / 'plan.json')

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://test'
        ) as client:
            html = {path: (await client.get(path)).text
                    for path in ('/dashboard/content', '/health', '/plan')}
        if conflict:
            assert '来源冲突，完整时长未确认' in html['/dashboard/content']
            assert '本次完整时长未确认' in html['/health']
            assert '睡眠来源冲突，未用于恢复评分' in html['/plan']
            assert '8.0h' not in html['/plan']
        else:
            assert '>8.0</strong><span>小时' in html['/dashboard/content']
            assert '>8.0小时</strong>' in html['/health']
            assert '>8.0h</b>' in html['/plan']
    asyncio.run(run())


@pytest.mark.parametrize('summary', [None, {'InBed': 480}, {'Awake': 60}])
def test_missing_asleep_evidence_is_not_zero_sleep(summary):
    from sleep_stats import confirmed_sleep_minutes
    assert confirmed_sleep_minutes(summary) is None
