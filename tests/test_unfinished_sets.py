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
