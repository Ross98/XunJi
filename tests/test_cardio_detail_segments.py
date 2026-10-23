from copy import deepcopy
from html import unescape
import re

import pytest

from app_state import get_jinja_env


def detail(movements, **extra):
    train = {'datestr': '2026-10-23', 'title': '合成分段训练', 'movements': movements, **extra}
    html = get_jinja_env().get_template('training_detail.html').render(
        trains=[train], date_str='2026-10-23', summary={'total_volume': 0}, cardio_summary={},
    )
    return unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html)))


def test_cardio_detail_keeps_every_segment_and_unfinished_flag():
    text = detail([{'name': '跑步', 'exetype': 'cardio', 'sets': [
        {'time': 600, 'calories': 100, 'avgHeartRate': 120},
        {'time': 1200, 'calories': 220, 'avgHeartRate': 150},
        {'time': 60, 'calories': 30, 'done': False},
    ]}])
    assert '第1段' in text and '第2段' in text and '第3段 · 未完成' in text
    assert '10分钟' in text and '20分钟' in text
    assert '100kcal' in text and '220kcal' in text and '30kcal' in text
    assert '平均 150' in text


def test_nested_cardio_metrics_and_short_duration_are_visible():
    text = detail([{'name': '跑步', 'exetype': 'cardio', 'sets': [{'items': [
        {'set': {'workoutTime': 15, 'metrics': {'calories': 8, 'distance': .05, 'maxHeartRate': 170}}},
        {'set': {'duration_s': 30, 'metrics': {'calories': 9}}},
    ]}]}])
    assert '0.25分钟' in text and '0.5分钟' in text
    assert '8kcal' in text and '9kcal' in text and '0.05km' in text
    assert '平均 --' in text and '最高 170' in text


@pytest.mark.parametrize('value', [0, '0'])
def test_cardio_zero_is_visible_without_requiring_positive_calories(value):
    text = detail([{'name': '骑行', 'exetype': 'cardio', 'sets': [
        {'time': 0, 'calories': value, 'distance': 0},
    ]}])
    assert '0分钟' in text and '0kcal' in text and '0km' in text
    assert '未记录' not in text


def test_cardio_bad_metrics_do_not_become_zero():
    text = detail([{'name': '骑行', 'exetype': 'cardio', 'sets': [
        {'time': 'bad', 'calories': True, 'distance': float('inf')},
    ]}])
    assert '指标未记录' in text
    assert '0分钟' not in text and '0kcal' not in text
    assert 'inf' not in text


def test_merged_training_keeps_original_cardio_segments_visible():
    text = detail([{'name': '卧推', 'sets': [{'weight': 50, 'reps': 10}]}],
                  _cardio_movements=[{'name': '骑行', 'exetype': 'cardio', 'sets': [
                      {'time': 180, 'calories': 25}, {'time': 360, 'calories': 50},
                  ]}])
    assert '3分钟' in text and '6分钟' in text
    assert '25kcal' in text and '50kcal' in text
    assert '50 × 10次' in text


@pytest.mark.parametrize('bad', [True, -1, None, 'bad', float('nan'), float('inf'), 10**400])
def test_cardio_display_projection_validates_without_mutating(bad):
    record = {'time': bad, 'metrics': {'calories': 25}}
    original = deepcopy(record)
    text = detail([{'name': '骑行', 'exetype': 'cardio', 'sets': [record]}])
    assert '分钟' not in text.replace('有氧分钟', '')
    assert '25kcal' in text
    assert record.keys() == original.keys()
    assert record['metrics'] == original['metrics']


def test_flat_zero_takes_precedence_over_nested_old_values():
    text = detail([{'name': '骑行', 'exetype': 'cardio', 'sets': [
        {'calories': 0, 'metrics': {'calories': 100}, 'time': '15'},
    ]}])
    assert '0kcal' in text and '100kcal' not in text
    assert '0.25分钟' in text


def test_cardio_missing_duration_is_not_rendered_as_zero_minutes():
    text = detail([{'name': '骑行', 'exetype': 'cardio', 'sets': [{'calories': 25}]}])
    assert '25kcal' in text and '0分钟' not in text


@pytest.mark.parametrize('preserved', ['bad', {'sets': []}, [None, 'bad']])
def test_invalid_preserved_container_does_not_break_valid_segments(preserved):
    text = detail([{'name': '骑行', 'exetype': 'cardio', 'sets': [{'time': 60, 'calories': 25}]}],
                  _cardio_movements=preserved)
    assert '1分钟' in text and '25kcal' in text
