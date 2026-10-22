import asyncio
from datetime import date, timedelta

from starlette.requests import Request

from routes import training


def test_page_chart_ranges_include_calendar_boundaries_and_exclude_future(monkeypatch, tmp_cache):
    today = date.today()
    context = {}
    for offset in (-1, 0, 29, 30, 89, 90):
        day = (today - timedelta(days=offset)).isoformat()
        rows = [{'datestr': day, 'start': 1000, 'end': 61000, 'movements': [
            {'name': '跑步', 'exetype': 'cardio', 'sets': [{'time': 60, 'calories': 10}]},
        ]}]
        tmp_cache.set(f'training:{day}:True', {'trains': rows})

    class Service:
        async def get_training(self, day, full=False):
            return tmp_cache.get(f'training:{day}:True') or {'trains': []}

    class Template:
        def render(self, **kwargs):
            context.update(kwargs)
            return 'ok'

    class Templates:
        def get_template(self, name):
            return Template()

    monkeypatch.setattr(training, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(training, 'get_data_service', Service)
    monkeypatch.setattr(training, 'get_jinja_env', Templates)
    monkeypatch.setattr(training, '_ensure_training_history_backfill', lambda *args: None)
    asyncio.run(training.training_page(Request({'type': 'http'})))
    charts = context['analysis_data']
    assert {item['date'] for item in charts['30']['duration']} == {
        today.isoformat(), (today - timedelta(days=29)).isoformat()}
    assert {item['date'] for item in charts['90']['duration']} == {
        (today - timedelta(days=offset)).isoformat() for offset in (0, 29, 30, 89)}
    assert charts['30']['type_dist'][0]['count'] == 2
    assert charts['90']['type_dist'][0]['count'] == 4


def test_calendar_window_keeps_all_sessions_on_a_day_and_crosses_years():
    from training_charts import build_chart_ranges
    reference = date(2026, 1, 2)
    trains = [{'datestr': '2025-12-31', 'start': 1, 'end': 60001,
               'movements': [{'name': '跑步', 'exetype': 'cardio', 'sets': [{'time': 60}]}]}
              for _ in range(40)]
    volume, analysis = build_chart_ranges(trains, trains, reference)
    assert len(analysis['30']['duration']) == 40
    assert analysis['30']['type_dist'][0]['count'] == 40


def test_weekly_capacity_uses_monday_without_utc_date_shift():
    from training_charts import build_chart_ranges
    trains = [{'datestr': day, 'movements': [{'name': '卧推', 'sets': [{'weight': 50, 'reps': 10}]}]}
              for day in ('2025-12-29', '2026-01-02')]
    volume, analysis = build_chart_ranges(trains, trains, date(2026, 1, 2))
    assert volume['90'] == [{'date': '2025-12-29', 'value': 1000}]
    assert volume['30'] == [{'date': '2025-12-29', 'value': 500}, {'date': '2026-01-02', 'value': 500}]


def test_invalid_and_future_record_dates_are_not_chart_samples():
    from training_charts import select_training_window
    reference = date(2026, 1, 2)
    rows = [{'datestr': value} for value in (None, 'broken', '2026-01-03', '2025-12-31')]
    assert select_training_window(rows, reference, 30) == [{'datestr': '2025-12-31'}]
