import asyncio
from datetime import date, timedelta

from fastapi import BackgroundTasks
from starlette.requests import Request

from routes import dashboard


def _render_dashboard_context(monkeypatch, ah, cache):
    class Service:
        async def get_training(self, *args, **kwargs):
            return {'trains': []}

        async def get_body(self, *args, **kwargs):
            return {'records': []}

    context = {}
    env = dashboard.get_jinja_env()
    template = env.get_template('dashboard.html')

    class Template:
        blocks = template.blocks

        def new_context(self, values):
            context.update(values)
            return template.new_context(values)

    class Templates:
        def get_template(self, name):
            return Template()

    monkeypatch.setattr(dashboard, 'get_data_service', Service)
    monkeypatch.setattr(dashboard, 'get_cache', lambda: cache)
    monkeypatch.setattr(dashboard, 'get_jinja_env', Templates)
    monkeypatch.setattr(dashboard, '_ah', ah)
    response = asyncio.run(dashboard.dashboard_content(
        Request({'type': 'http', 'path': '/dashboard/content', 'headers': []}), BackgroundTasks()))
    return response, context


def test_dashboard_does_not_display_overlapping_sources_as_a_daily_total(
    monkeypatch, isolated_health_db, tmp_cache
):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    ah = isolated_health_db
    with ah._conn() as conn:
        for metric in ('HKQuantityTypeIdentifierStepCount', 'HKQuantityTypeIdentifierActiveEnergyBurned'):
            for source, begin, end, value in [('watch','08:00:00','09:00:00',100),
                                               ('phone','08:30:00','09:30:00',80)]:
                conn.execute('INSERT INTO health_records (type,start_date,end_date,value,source_name) VALUES (?,?,?,?,?)',
                             (metric, yesterday+' '+begin+' +0800', yesterday+' '+end+' +0800', value, source))

    response, context = _render_dashboard_context(monkeypatch, ah, tmp_cache)
    assert context['health']['today_steps'] is None
    assert context['health']['steps_overlap'] is True
    assert context['health']['step_avg'] is None
    assert context['health']['step_avg_days'] == 0
    assert context['yesterday_active_calories'] is None
    assert context['yesterday_active_overlap'] is True
    assert '来源重叠，日总量未确认' in response.body.decode()


def test_bad_activity_interval_does_not_confirm_total_or_hide_other_health_metrics(
    monkeypatch, isolated_health_db, tmp_cache
):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    ah = isolated_health_db
    with ah._conn() as conn:
        for metric in ('HKQuantityTypeIdentifierStepCount', 'HKQuantityTypeIdentifierActiveEnergyBurned'):
            conn.execute('INSERT INTO health_records (type,start_date,end_date,value,source_name) VALUES (?,?,?,?,?)',
                         (metric, yesterday+' 08:00:00 +0800', yesterday+' 07:00:00 +0800', 100, 'synthetic'))
        conn.execute('INSERT INTO health_records (type,start_date,end_date,value,source_name) VALUES (?,?,?,?,?)',
                     ('HKQuantityTypeIdentifierHeartRateVariabilitySDNN', yesterday+' 09:00:00 +0800',
                      yesterday+' 09:01:00 +0800', 42, 'synthetic'))

    response, context = _render_dashboard_context(monkeypatch, ah, tmp_cache)
    assert response.status_code == 200
    assert context['health']['has_data'] is True
    assert context['health']['today_steps'] is None
    assert context['health']['step_avg'] is None
    assert context['health']['step_avg_days'] == 0
    assert context['health']['latest_hrv'] == 42
    assert context['yesterday_active_calories'] is None


def test_confirmed_zero_baseline_is_explained_without_claiming_missing_data(
    monkeypatch, isolated_health_db, tmp_cache
):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    rows = [dict(type='HKQuantityTypeIdentifier'+metric, start_date=yesterday+' 08:00:00 +0800',
                 end_date=yesterday+' 09:00:00 +0800', value=0,unit='count',source_name='synthetic')
            for metric in ('StepCount','AppleExerciseTime','AppleStandTime')]
    isolated_health_db._bulk_insert(rows, [])
    response, context = _render_dashboard_context(monkeypatch, isolated_health_db, tmp_cache)
    for metric in ('today_steps','today_exercise','today_stand','step_avg','ex_avg','stand_avg'):
        assert context['health'][metric] == 0
    assert response.body.decode().count('日均为0，暂不比较百分比') == 3
