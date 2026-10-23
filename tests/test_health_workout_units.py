"""Workout labels must retain measurement units, missing values and real zeroes."""

import asyncio
from datetime import date

import pytest
from starlette.requests import Request

from routes import health


@pytest.mark.parametrize('duration,unit,energy,energy_unit,expected,absent', [
    (30, 's', 12, 'kJ', ['30秒', '12 kJ'], ['30分钟', '12 kcal']),
    (1.5, 'h', 0, 'kcal', ['1.5小时', '0 kcal'], ['2分钟']),
    (0.25, 'min', None, None, ['0.25分钟'], ['0分钟', 'kcal']),
    (None, 'min', None, None, ['时长未记录'], ['0分钟', 'kcal']),
    (30, '', 12, '', ['30（单位未记录）', '12（单位未记录）'], ['30分钟', '12 kcal']),
    (30, 'custom', 12, '<unknown>', ['30 custom', '12 &lt;unknown&gt;'], ['30分钟', '12 kcal']),
])
def test_workout_labels_preserve_units_and_missing_values(
    isolated_health_db, tmp_cache, monkeypatch, tmp_path,
    duration, unit, energy, energy_unit, expected, absent,
):
    ah = isolated_health_db
    day = date.today().isoformat()
    ah._bulk_insert([], [dict(
        workout_activity_type='HKWorkoutActivityTypeWalking',
        start_date=day + ' 08:00:00 +0800', end_date=day + ' 08:30:00 +0800',
        duration=duration, duration_unit=unit, total_distance=None, total_distance_unit=None,
        total_energy_burned=energy, total_energy_burned_unit=energy_unit, source_name='synthetic',
    )])
    monkeypatch.setattr(health, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(health.hs, 'source_folder', lambda: tmp_path)
    response = asyncio.run(health.health_page(Request({'type': 'http', 'path': '/health', 'headers': []})))
    html = response.body.decode()
    for text in expected:
        assert text in html
    for text in absent:
        assert text not in html
    # Rendering is read-only: exported source values and units remain intact.
    workout = ah.query_workouts()[0]
    assert (workout['duration'], workout['duration_unit']) == (duration, unit)
    assert (workout['total_energy_burned'], workout['total_energy_burned_unit']) == (energy, energy_unit)
