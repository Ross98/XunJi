"""Legacy numeric corruption must not break JSON responses or mutate raw health data."""

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from routes import health


@pytest.mark.parametrize('field', ['value', 'duration', 'total_distance', 'total_energy_burned'])
@pytest.mark.parametrize('invalid', [None, float('inf'), float('-inf'), 'bad'])
def test_health_query_serializes_invalid_measurements_as_null(
    isolated_health_db, field, invalid
):
    ah = isolated_health_db
    common = dict(start_date='2026-08-09 08:00:00 +0800',
                  end_date='2026-08-09 08:30:00 +0800', source_name='synthetic')
    metric = 'HKQuantityTypeIdentifierHeartRateVariabilitySDNN'
    if field == 'value':
        ah._bulk_insert([dict(common, type=metric, value=invalid, unit='ms')], [])
        endpoint = '/health/data'
        read = lambda: ah.query_records(metric)
    else:
        workout = dict(common, workout_activity_type='HKWorkoutActivityTypeWalking',
            duration=0, duration_unit='min', total_distance=0, total_distance_unit='km',
            total_energy_burned=0, total_energy_burned_unit='kcal')
        workout[field] = invalid
        ah._bulk_insert([], [workout])
        endpoint = '/health/workouts'
        read = ah.query_workouts
    valid = dict(read()[0], **{field: 0},
                 start_date='2026-08-09 09:00:00 +0800', end_date='2026-08-09 09:30:00 +0800')
    ah._bulk_insert([valid] if field == 'value' else [], [valid] if field != 'value' else [])
    original = read()
    app = FastAPI()
    app.include_router(health.router)

    async def run():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url='http://local') as client:
            response = await client.get(endpoint)
            assert response.status_code == 200
            rows = response.json()['data']
            assert rows == [dict(row, **{field: None}) if row['start_date'] == common['start_date']
                            else row for row in original]
            assert 'Infinity' not in response.text
            assert 'NaN' not in response.text

    asyncio.run(run())
    assert read() == original
