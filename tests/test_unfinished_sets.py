from copy import deepcopy
from datetime import date

from analysis import (iter_movement_sets, summarize_training, movement_history,
                      session_metrics, training_type_distribution, movement_frequency)
from planner import generate_plan


def movements():
    return [{'name': '卧推', 'sets': [
        {'done': True, 'weight': 50, 'reps': 10},
        {'done': False, 'weight': 150, 'reps': 20},
        {'items': [{'set': {'done': False, 'weight': 100, 'reps': 20}},
                   {'set': {'weight': 40, 'reps': 10}}]},
    ]}]


def test_unfinished_sets_do_not_inflate_completed_statistics():
    trains = [{'datestr': '2026-10-22', 'movements': movements()}]
    original = deepcopy(trains)
    summary = summarize_training(trains)
    assert summary['total_sets'] == 2
    assert summary['total_volume'] == 900
    row, = movement_history(trains, '卧推')
    assert row['total_sets'] == 2
    assert row['max_weight'] == 50
    assert row['total_volume'] == 900
    plan = generate_plan(trains, reference_date=date(2026, 10, 23))
    assert next(g for g in plan['groups'] if g['key'] == 'push')['sets'] == 2
    assert trains == original


def test_unfinished_cardio_metrics_and_types_are_excluded():
    train = {'datestr': '2026-10-22', 'movements': [
        {'name': '跑步', 'exetype': 'cardio', 'sets': [
            {'done': False, 'time': 600, 'calories': 500, 'avgHeartRate': 180}]},
        {'name': '步行', 'exetype': 'cardio', 'sets': [
            {'done': True, 'time': 60, 'calories': 10, 'avgHeartRate': 100}]},
    ]}
    metrics = session_metrics(train)
    assert metrics['calories'] == 10
    assert metrics['avgHeartRate'] == 100
    assert [r['name'] for r in training_type_distribution([train])] == ['步行']
    assert movement_frequency([train]) == {'步行': 1}
    assert movement_history([train], '跑步') == []


def test_raw_sets_still_available_for_detail_without_mutation():
    movement = movements()[0]
    assert len(list(iter_movement_sets(movement, include_unfinished=True))) == 4
    assert len(list(iter_movement_sets(movement))) == 2


def test_empty_legacy_set_is_not_mistaken_for_unfinished():
    from analysis import movement_is_unfinished
    assert not movement_is_unfinished({'sets': [{}]})
    # A partially completed group can contain an explicitly completed child.
    child = {'done': True, 'weight': 50, 'reps': 10}
    assert list(iter_movement_sets({'sets': [{'done': False, 'items': [
        {'set': child}]}]})) == [child]


def test_detail_preserves_and_labels_unfinished_sets(monkeypatch, tmp_cache):
    import asyncio
    from starlette.requests import Request
    from routes import training

    class Service:
        async def get_training(self, *args, **kwargs):
            return {'trains': [{'datestr': '2026-10-22', 'movements': movements()}]}

    monkeypatch.setattr(training, 'get_data_service', Service)
    monkeypatch.setattr(training, 'get_cache', lambda: tmp_cache)
    response = asyncio.run(training.training_detail_fragment(
        Request({'type': 'http', 'path': '/training/2026-10-22', 'headers': []}), '2026-10-22'))
    html = response.body.decode()
    assert html.count('未完成') == 2
    assert '150' in html and '100' in html
    assert '>900<' in html


def test_wholly_unfinished_session_does_not_create_training_day_or_time():
    from analysis import training_frequency_by_date, training_duration_trend, training_time_coverage
    trains = [{'datestr': '2026-10-22', 'start': 0, 'end': 3600000, 'movements': [
        {'name': '卧推', 'sets': [{'done': False, 'weight': 50, 'reps': 10}]},
    ]}]
    assert summarize_training(trains)['total_days'] == 0
    assert training_frequency_by_date(trains) == []
    assert training_duration_trend(trains) == []
    assert training_time_coverage(trains)['minutes'] is None
    assert generate_plan(trains)['groups'] == []


def test_pending_cardio_cannot_extend_completed_manual_session_bounds():
    from routes.training import _merge_overlapping_trains
    from analysis import training_time_coverage
    trains = [
        {'datestr': '2026-10-22', 'start': 0, 'end': 3600000, 'movements': [
            {'name': '卧推', 'sets': [{'done': True}]}]},
        {'datestr': '2026-10-22', 'start': 60000, 'end': 7200000, 'movements': [
            {'name': '步行', 'exetype': 'cardio', 'sets': [{'done': False, 'time': 7140}]}]},
    ]
    merged = _merge_overlapping_trains(trains)
    assert len(merged) == 2
    assert training_time_coverage(merged)['minutes'] == 60


def test_pending_records_remain_visible_without_claiming_completed_sessions(monkeypatch, tmp_cache):
    import asyncio
    from starlette.requests import Request
    from routes import training
    day = '2026-10-22'
    payload = {'trains': [{'datestr': day, 'movements': [
        {'name': '卧推', 'sets': [{'done': False, 'weight': 50, 'reps': 10}]},
    ]}]}
    tmp_cache.set(f'training:{day}:True', payload)

    class Service:
        async def get_training(self, *args, **kwargs):
            return payload

    monkeypatch.setattr(training, 'get_data_service', Service)
    monkeypatch.setattr(training, 'get_cache', lambda: tmp_cache)
    response = asyncio.run(training.training_detail_fragment(
        Request({'type': 'http', 'path': '/training/'+day, 'headers': []}), day))
    html = response.body.decode()
    assert '未完成记录' in html
    assert '计入统计场次' in html
    assert '卧推' in html and '50' in html
    assert '无训练记录' not in html
    assert '有氧分钟' not in html
    assert day in training._get_cached_training_dates()


def test_missing_legacy_completion_flags_are_not_inferred_as_unfinished():
    from analysis import training_frequency_by_date, training_is_unfinished
    train = {'datestr': '2026-10-22', 'movements': [
        {'name': '卧推', 'sets': [{'weight': 50, 'reps': 10}]}]}
    assert not training_is_unfinished(train)
    assert summarize_training([train])['total_days'] == 1
    assert training_frequency_by_date([train]) == ['2026-10-22']
    assert generate_plan([train])['groups']


def test_training_page_excludes_pending_day_from_frequency_but_keeps_calendar_access(monkeypatch, tmp_cache):
    import asyncio
    from datetime import timedelta
    from html.parser import HTMLParser
    from starlette.requests import Request
    from routes import training, dashboard
    pending_day = date.today().isoformat()
    completed_day = (date.today() - timedelta(days=1)).isoformat()
    pending = {'trains': [{'datestr': pending_day, 'movements': [
        {'name': '卧推', 'sets': [{'done': False, 'weight': 50, 'reps': 10}]}]}]}
    completed = {'trains': [{'datestr': completed_day, 'movements': [
        {'name': '卧推', 'sets': [{'done': True, 'weight': 50, 'reps': 10}]}]}]}
    tmp_cache.set(f'training:{pending_day}:True', pending)
    tmp_cache.set(f'training:{completed_day}:True', completed)

    class Service:
        async def get_training(self, *args, **kwargs):
            return pending

    context = {}
    template = training.get_jinja_env().get_template('training.html')

    class Template:
        def render(self, **values):
            context.update(values)
            return template.render(**values)

    class Templates:
        def get_template(self, name):
            return Template()

    monkeypatch.setattr(training, 'get_data_service', Service)
    monkeypatch.setattr(training, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(training, 'get_jinja_env', Templates)
    monkeypatch.setattr(training, '_ensure_training_history_backfill', lambda *args: None)
    response = asyncio.run(training.training_page(Request({'type': 'http', 'path': '/training', 'headers': []})))
    assert context['freq_dates'] == [completed_day]
    assert context['freq_counts'] == {completed_day: 1}
    assert context['freq_stats']['days'] == 1
    assert context['session_count'] == 0
    assert context['summary']['total_days'] == 0
    assert dashboard._get_cached_training_dates(tmp_cache, pending_day, 2) == {completed_day}

    class CalendarCell(HTMLParser):
        attributes = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'td' and attrs.get('data-date') == pending_day:
                self.attributes = attrs

    cell = CalendarCell()
    cell.feed(response.body.decode())
    assert cell.attributes['hx-get'] == '/training/'+pending_day
    assert '仅未完成记录' in cell.attributes['aria-label']
    assert 'cal-load-' not in cell.attributes['class']
