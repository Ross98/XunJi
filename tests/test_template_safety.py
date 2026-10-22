import asyncio
import json
import re
from datetime import date, timedelta

from fastapi import BackgroundTasks
from markupsafe import escape
import pytest
from starlette.requests import Request

from routes import dashboard, training


def test_training_detail_escapes_api_text(monkeypatch, tmp_cache):
    text = '<b data-test="raw">名称 & 备注</b>'

    class Service:
        async def get_training(self, *args, **kwargs):
            return {'trains': [{'datestr': '2026-10-22', 'title': text, 'note': {'text': text},
                                'movements': [{'name': text, 'sets': [{'weight': 60, 'reps': 10}]}]}]}

    monkeypatch.setattr(training, 'get_data_service', Service)
    monkeypatch.setattr(training, 'get_cache', lambda: tmp_cache)
    response = asyncio.run(training.training_detail_fragment(
        Request({'type': 'http', 'path': '/training/2026-10-22', 'headers': []}), '2026-10-22'))
    html = response.body.decode()
    assert text not in html
    assert str(escape(text)) in html


@pytest.mark.parametrize('same_day', [True, False])
def test_recovery_chart_emits_valid_json_for_missing_baseline(
    monkeypatch, isolated_health_db, tmp_cache, same_day
):
    today = date.today().isoformat()
    training_day = today if same_day else (date.today() - timedelta(days=1)).isoformat()
    tmp_cache.set(f'training:{training_day}:True', {'trains': [{'datestr': training_day, 'movements': []}]})
    ah = isolated_health_db
    with ah._conn() as conn:
        conn.execute('INSERT INTO health_records (type,start_date,end_date,value) VALUES (?,?,?,?)',
                     ('HKQuantityTypeIdentifierHeartRateVariabilitySDNN', today + ' 08:00:00 +0800',
                      today + ' 08:01:00 +0800', 40))

    class Service:
        async def get_training(self, *args, **kwargs):
            return {'trains': []}

        async def get_body(self, *args, **kwargs):
            return {'records': []}

    monkeypatch.setattr(dashboard, 'get_data_service', Service)
    monkeypatch.setattr(dashboard, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(dashboard, '_ah', ah)
    response = asyncio.run(dashboard.dashboard_content(
        Request({'type': 'http', 'path': '/dashboard/content', 'headers': []}), BackgroundTasks()))
    html = response.body.decode()
    constants = {name: json.loads(re.search(r'const ' + name + r' = (.*);', html).group(1))
                 for name in ('trainRef', 'restRef', 'recData', 'HRV_DATA')}
    assert constants['restRef' if same_day else 'trainRef'] is None
    assert isinstance(constants['recData'], list)
    assert isinstance(constants['HRV_DATA'], list)


def test_preferences_preserve_text_without_breaking_input_attributes(monkeypatch, isolated_health_db, tmp_cache, tmp_path):
    from html.parser import HTMLParser
    from routes import plan
    value = '\"><b data-test="attribute">text & quote</b>'
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps({'equipment': value}))
    monkeypatch.setattr(plan, 'PLAN_CONFIG_PATH', path)
    monkeypatch.setattr(plan, 'get_cache', lambda: tmp_cache)
    response = asyncio.run(plan.plan_page(Request({'type': 'http', 'path': '/plan', 'headers': []})))

    class Parser(HTMLParser):
        equipment = None

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == 'input' and attributes.get('id') == 'planEquipment':
                self.equipment = attributes.get('value')

    parser = Parser()
    parser.feed(response.body.decode())
    assert parser.equipment == value
    assert value not in response.body.decode()


def test_training_page_embeds_names_as_safe_json_and_escaped_text(monkeypatch, tmp_cache):
    name = '</script><b>标记</b>'
    today = date.today().isoformat()
    payload = {'trains': [{'datestr': today, 'movements': [
        {'name': name, 'sets': [{'weight': 60, 'reps': 10}]},
    ]}]}

    class Service:
        async def get_training(self, day, full=False):
            return payload if day == today else {'trains': []}

    tmp_cache.set(f'training:{today}:True', payload)
    monkeypatch.setattr(training, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(training, 'get_data_service', Service)
    monkeypatch.setattr(training, '_ensure_training_history_backfill', lambda *args: None)
    response = asyncio.run(training.training_page(Request({'type': 'http', 'path': '/training', 'headers': []})))
    html = response.body.decode()
    assert name not in html
    assert str(escape(name)) in html
    names = json.loads(re.search(r'const movementNames = (.*);', html).group(1))
    assert names == [name]
