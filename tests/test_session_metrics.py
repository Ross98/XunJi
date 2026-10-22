from copy import deepcopy

import pytest

from analysis import calories_trend, heart_rate_trend, training_duration_trend
from routes.training import _merge_overlapping_trains


def cardio(start, end, calories, avg, maximum, distance):
    return {'datestr': '2026-10-22', 'start': start * 60000, 'end': end * 60000,
            'movements': [{'name': '有氧', 'exetype': 'cardio', 'sets': [{
                'time': (end-start)*60, 'calories': calories, 'avgHeartRate': avg,
                'maxHeartRate': maximum, 'distance': distance,
            }]}]}


def test_merge_preserves_metrics_from_all_cardio_segments_without_duplication():
    manual = {'datestr': '2026-10-22', 'start': 0, 'end': 60*60000,
              'movements': [{'name': '卧推', 'exetype': 'weight', 'sets': []}]}
    trains = [manual, cardio(5, 15, 100, 120, 170, 1), cardio(20, 50, 200, 160, 165, 3)]
    original = deepcopy(trains)
    merged = _merge_overlapping_trains(trains)
    assert trains == original
    assert len(merged) == 1
    metrics = merged[0]['_cardio_metrics']
    assert metrics['calories'] == 300
    assert metrics['distance'] == 4
    assert metrics['duration_s'] == 2400
    assert metrics['total_duration_min'] == 60
    assert metrics['avgHeartRate'] == 150
    assert metrics['maxHeartRate'] == 170
    assert calories_trend(merged)[0]['calories'] == 300
    assert heart_rate_trend(merged)[0]['avg_hr'] == 150
    assert heart_rate_trend(merged)[0]['max_hr'] == 170


def test_multiple_average_heart_rates_need_durations_for_weighting():
    train = {'datestr': '2026-10-22', 'movements': [{'sets': [
        {'avgHeartRate': 120, 'maxHeartRate': 170},
        {'avgHeartRate': 160, 'maxHeartRate': 165},
    ]}]}
    row, = heart_rate_trend([train])
    assert row['avg_hr'] is None
    assert row['max_hr'] == 170


def test_heart_rate_uses_duration_weighting_and_flat_metrics_take_precedence():
    train = {'datestr': '2026-10-22', 'movements': [{'sets': [
        {'avgHeartRate': 120, 'time': 600, 'metrics': {'avgHeartRate': 180}},
        {'metrics': {'avgHeartRate': 160, 'maxHeartRate': 170}, 'workoutTime': 1800},
    ]}]}
    row, = heart_rate_trend([train])
    assert row['avg_hr'] == 150
    assert row['max_hr'] == 170


@pytest.mark.parametrize('bad', [None, 'bad', float('nan'), float('inf'), True, 10**400])
def test_invalid_session_times_do_not_merge_or_crash(bad):
    first = cardio(5, 15, 100, 120, 170, 1)
    second = {'datestr': '2026-10-22', 'start': bad, 'end': 20*60000,
              'movements': [{'name': '卧推', 'exetype': 'weight', 'sets': []}]}
    assert len(_merge_overlapping_trains([first, second])) == 2
    assert training_duration_trend([second]) == []


def test_epoch_zero_is_valid_and_negative_durations_are_excluded():
    assert training_duration_trend([{'start': 0, 'end': 60000}])[0]['duration_min'] == 1
    assert training_duration_trend([{'start': 60000, 'end': 1}]) == []


def test_detail_counts_cardio_duration_instead_of_entire_merged_session(monkeypatch, tmp_cache):
    import asyncio
    from routes import training
    from starlette.requests import Request
    context = {}
    trains = [
        {'datestr': '2026-10-22', 'start': 0, 'end': 60*60000,
         'movements': [{'name': '徒手力量', 'exetype': 'weight', 'sets': []}]},
        cardio(5, 15, 100, 120, 170, 1), cardio(20, 50, 200, 160, 165, 3),
    ]

    class Service:
        async def get_training(self, *args, **kwargs):
            return {'trains': trains}

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
    asyncio.run(training.training_detail_fragment(Request({'type': 'http'}), '2026-10-22'))
    assert context['cardio_summary'] == {'minutes': 40, 'distance': 4, 'calories': 300}


def test_partial_heart_rate_is_displayed_without_inventing_zero_peak():
    from app_state import get_jinja_env
    train = cardio(5, 15, 100, 120, None, 1)
    template = get_jinja_env().get_template('training_detail.html')
    html = template.render(trains=[train], date_str='2026-10-22',
                           summary={'total_days': 1, 'total_volume': 0}, cardio_summary={})
    assert '最高 --' in html
    assert '120-0bpm' not in html


def test_training_analysis_accepts_invalid_times_and_nested_calories():
    from analysis import training_type_distribution
    trains = [{'datestr': '2026-10-22', 'start': None, 'end': 60000,
               'movements': [{'name': '跑步', 'exetype': 'cardio', 'sets': [
                   {'items': [{'set': {'metrics': {'calories': 100}}}]},
               ]}]}]
    assert training_type_distribution(trains, cardio_only=True) == [
        {'name': '跑步', 'count': 1, 'duration_min': 0, 'calories': 100}]
