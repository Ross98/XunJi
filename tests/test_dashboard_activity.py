import asyncio
from datetime import date, timedelta

from fastapi import BackgroundTasks
from starlette.requests import Request

from routes import dashboard


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
    monkeypatch.setattr(dashboard, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(dashboard, 'get_jinja_env', Templates)
    monkeypatch.setattr(dashboard, '_ah', ah)
    response = asyncio.run(dashboard.dashboard_content(
        Request({'type': 'http', 'path': '/dashboard/content', 'headers': []}), BackgroundTasks()))
    assert context['health']['today_steps'] is None
    assert context['health']['steps_overlap'] is True
    assert context['health']['step_avg'] is None
    assert context['health']['step_avg_days'] == 0
    assert context['yesterday_active_calories'] is None
    assert context['yesterday_active_overlap'] is True
    assert '来源重叠，日总量未确认' in response.body.decode()
