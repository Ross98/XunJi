import asyncio
from datetime import date, timedelta

import pytest
from fastapi import BackgroundTasks
from starlette.requests import Request

from routes import dashboard


def movement(kind='weight'):
    return {'name': '卧推' if kind == 'weight' else '步行', 'exetype': kind,
            'sets': [{'done': True, 'weight': 50, 'reps': 10}]}


@pytest.mark.parametrize('records,expected_count,minutes,incomplete', [
    ([{'start': 0, 'end': 3600000, 'movements': [movement()]}], 1, 60, False),
    ([{'start': 0, 'end': 3600000, 'movements': [movement()]},
      {'start': 60000, 'end': 1800000, 'movements': [movement('cardio')]}], 1, 60, False),
    ([{'start': 0, 'end': 3600000, 'movements': [movement('cardio')]},
      {'start': 1800000, 'end': 5400000, 'movements': [movement('cardio')]}], 2, 90, False),
    ([{'start': 0, 'end': 3600000, 'movements': [movement()]},
      {'movements': [movement()]}], 2, 60, True),
    ([{'movements': [movement()]}], 1, None, True),
    ([{'start': 0, 'end': 120000, 'movements': []}], 1, 2, False),
    ([{'start': True, 'end': 120000, 'movements': []}], 1, None, True),
    ([{'start': 0, 'end': 600, 'movements': []}], 1, .01, False),
    ([{'start': 0, 'end': 3600000, 'movements': [
        {'name': '卧推', 'sets': [{'done': False, 'weight': 50, 'reps': 10}]}]}], 0, None, False),
])
def test_dashboard_training_matches_session_grouping_and_observed_time(
    records, expected_count, minutes, incomplete, monkeypatch, isolated_health_db, tmp_cache
):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    records = [dict(record, datestr=yesterday) for record in records]

    class Service:
        async def get_training(self, *args, **kwargs):
            return {'trains': records}

        async def get_body(self, *args, **kwargs):
            return {'records': []}

    context = {}
    template = dashboard.get_jinja_env().get_template('dashboard.html')

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
    monkeypatch.setattr(dashboard, '_ah', isolated_health_db)
    response = asyncio.run(dashboard.dashboard_content(
        Request({'type': 'http', 'path': '/dashboard/content', 'headers': []}), BackgroundTasks()))
    assert context['training_sessions'] == expected_count
    assert context['training_minutes'] == minutes
    assert context['training_duration_incomplete'] is incomplete
    html = response.body.decode()
    if expected_count == 0:
        assert '未完成记录，不计统计' in html
    elif minutes is None:
        assert '时长未确认' in html
    elif incomplete:
        assert '已知覆盖' in html
    else:
        assert '覆盖' in html
        if minutes < 1:
            assert '不足1分钟' in html
