"""Import timestamps use absolute time and full precision; invalid intervals never project."""
import asyncio
from copy import deepcopy
import xml.etree.ElementTree as ET

import httpx
import pytest
from fastapi import FastAPI

from routes import health
from test_hozz import batch, sample

INVALID = [
    {'startDate': ''}, {'startDate': 'not-a-date'},
    {'startDate': '2026-10-23'},
    {'startDate': '2026-10-23T08:00:00'},
    {'startDate': '2026-10-23T09:00:00Z', 'endDate': '2026-10-23T08:00:00Z'},
    {'startDate': '2026-10-23T08:00:00.500Z', 'endDate': '2026-10-23T08:00:00.125Z'},
    {'startDate': '2026-10-23T08:00:00Z', 'endDate': '2026-10-23T09:00:00+02:00'},
    {'startDate': '2026-10-23T08:00:00.123456789Z', 'endDate': '2026-10-23T08:00:00.123456788Z'},
]


def hoz_sample(identifier='bad', kind='quantity', **changes):
    extra = {}
    if kind == 'category': extra = {'type': 'HKCategoryTypeIdentifierSleepAnalysis', 'value': 4}
    if kind == 'workout': extra = {'type': 'HKWorkoutTypeIdentifier', 'activityType': 37, 'duration': 60}
    return sample(identifier, kind, **{**extra, **changes})


@pytest.mark.parametrize('kind', ['quantity', 'category', 'workout'])
@pytest.mark.parametrize('changes', INVALID)
def test_hozz_invalid_interval_rolls_back_batch_without_blocking_neighbors(
    isolated_health_db, tmp_path, kind, changes
):
    import hozz_sync as hs
    folder = tmp_path / 'batches'
    batch(folder, 'a.ndjson', [sample('rolled-back'), hoz_sample(kind=kind, **changes)])
    batch(folder, 'b.ndjson', [sample('valid')])
    result = hs.sync(folder)
    assert result['status'] == 'partial'
    assert (result['files'], result['new_samples'], result['inserted']) == (1, 1, 1)
    with isolated_health_db._conn() as conn:
        assert [r[0] for r in conn.execute('SELECT id FROM hozz_samples')] == ['valid']


def xml_node(kind, **changes):
    common = dict(startDate='2026-10-23 08:00:00 +0800', endDate='2026-10-23 08:30:00 +0800')
    if kind == 'quantity': attrs = dict(common, type='steps', value='0', unit='count')
    elif kind == 'category': attrs = dict(common, type='HKCategoryTypeIdentifierSleepAnalysis', value='HKCategoryValueSleepAnalysisAsleepCore')
    else: attrs = dict(common, workoutActivityType='Walking', duration='30', durationUnit='min', totalDistance='0', totalEnergyBurned='0')
    return ET.Element('Workout' if kind == 'workout' else 'Record', {**attrs, **changes})


@pytest.mark.parametrize('kind', ['quantity', 'category', 'workout'])
@pytest.mark.parametrize('changes', INVALID)
def test_xml_skips_invalid_interval_but_keeps_valid_neighbor(isolated_health_db, tmp_path, monkeypatch, kind, changes):
    source = tmp_path / 'synthetic.xml'
    root = ET.Element('HealthData')
    root.extend([xml_node(kind, **changes), xml_node(kind, startDate='2026-10-23 09:00:00 +0800', endDate='2026-10-23 09:30:00 +0800')])
    ET.ElementTree(root).write(source, encoding='utf-8')
    monkeypatch.setattr(isolated_health_db, 'EXPORT_XML', source)
    assert isolated_health_db.import_all() == 1
    query = {'quantity': isolated_health_db.query_records, 'category': isolated_health_db.query_sleep,
             'workout': isolated_health_db.query_workouts}[kind]
    assert len(query()) == 1
    assert query()[0]['start_date'].startswith('2026-10-23 09:00:00')


@pytest.mark.parametrize('field', ['duration', 'totalDistance', 'totalEnergyBurned'])
def test_xml_rejects_negative_workout_summaries_but_retains_zero(isolated_health_db, tmp_path, monkeypatch, field):
    source = tmp_path / 'synthetic.xml'
    root = ET.Element('HealthData')
    root.extend([xml_node('workout', **{field: '-1'}), xml_node('workout',
        startDate='2026-10-23 09:00:00 +0800', endDate='2026-10-23 09:00:00 +0800', duration='0')])
    ET.ElementTree(root).write(source, encoding='utf-8')
    monkeypatch.setattr(isolated_health_db, 'EXPORT_XML', source)
    assert isolated_health_db.import_all() == 1
    assert isolated_health_db.query_workouts()[0]['duration'] == 0


def test_hozz_rejects_negative_duration_and_retries_repaired_file(isolated_health_db, tmp_path):
    import hozz_sync as hs
    folder = tmp_path / 'batches'
    batch(folder, 'a.ndjson', [hoz_sample(kind='workout', duration=-1)])
    assert hs.sync(folder)['status'] == 'partial'
    assert isolated_health_db.query_workouts() == []
    batch(folder, 'a.ndjson', [hoz_sample(kind='workout', duration=0)])
    assert hs.sync(folder)['status'] == 'ok'
    assert isolated_health_db.query_workouts()[0]['duration'] == 0


@pytest.mark.parametrize('kind', ['quantity', 'category', 'workout'])
def test_hozz_preserves_zero_intervals_and_compares_different_offsets_correctly(isolated_health_db, tmp_path, kind):
    import hozz_sync as hs
    folder = tmp_path / 'batches'
    # Local clock moves backwards, absolute time advances by one minute.
    first = hoz_sample('offsets', kind, startDate='2026-10-23T10:00:00+02:00', endDate='2026-10-23T09:01:00+01:00')
    second = hoz_sample('instant', kind, startDate='2026-10-23T09:00:00Z', endDate='2026-10-23T09:00:00Z')
    batch(folder, 'a.ndjson', [first, second])
    assert hs.sync(folder)['new_samples'] == 2
    assert hs.status()['unprojected'] == 0


@pytest.mark.parametrize('field', ['duration', 'total_distance', 'total_energy_burned'])
def test_legacy_negative_workout_values_are_unknown_in_api_without_mutating_storage(isolated_health_db, field):
    row = dict(workout_activity_type='HKWorkoutActivityTypeWalking', start_date='2026-10-23 08:00:00 +0800',
        end_date='2026-10-23 08:30:00 +0800', duration=30, duration_unit='min', total_distance=0,
        total_distance_unit='km', total_energy_burned=0, total_energy_burned_unit='kcal', source_name='synthetic')
    row[field] = -1
    isolated_health_db._bulk_insert([], [row])
    original = deepcopy(isolated_health_db.query_workouts())
    app = FastAPI()
    app.include_router(health.router)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
            response = await client.get('/health/workouts')
            assert response.status_code == 200
            assert response.json()['data'] == [dict(original[0], **{field: None})]
    asyncio.run(run())
    assert isolated_health_db.query_workouts() == original


@pytest.mark.parametrize('begin,end', [
    ('2026-10-23T08:00:00.1Z', '2026-10-23T08:00:00.10Z'),
    ('2026-10-23T08:00:00.123456788Z', '2026-10-23T08:00:00.123456789Z'),
    ('2026-10-23T10:00:00.123456788+02:00', '2026-10-23T09:00:00.123456789+01:00'),
    ('2026-10-23 08:00:00 +0800', '2026-10-23T00:00:00Z'),
])
def test_compatible_xml_and_iso_precision_is_accepted_without_rounding(begin, end):
    from health_values import health_interval
    assert health_interval(begin, end)


def test_invalid_legacy_survivor_does_not_block_deletion_or_reproject_bad_interval(isolated_health_db, tmp_path):
    import json
    import hozz_sync as hs
    folder = tmp_path / 'batches'
    owner = sample('owner')
    survivor = sample('legacy-invalid', quantity={'value': 200, 'unit': 'count'})
    batch(folder, 'a.ndjson', [owner, survivor])
    hs.sync(folder)
    survivor['endDate'] = '2026-09-29T15:00:00Z'
    with isolated_health_db._conn() as conn:
        conn.execute('UPDATE hozz_samples SET raw=? WHERE id=?', (json.dumps(survivor), survivor['id']))
    batch(folder, 'b.ndjson', [dict(schemaVersion=1, kind='deletion', type=owner['type'], id=owner['id'])])
    result = hs.sync(folder)
    assert result['status'] == 'ok'
    assert result['deleted'] == 1
    assert isolated_health_db.query_records() == []
    assert hs.status()['samples'] == hs.status()['unprojected'] == 1


def test_negative_signed_quantity_remains_valid(isolated_health_db, tmp_path, monkeypatch):
    import hozz_sync as hs
    typ = 'HKQuantityTypeIdentifierAppleSleepingWristTemperature'
    folder = tmp_path / 'batches'
    batch(folder, 'a.ndjson', [sample(type=typ, quantity={'value': -0.5, 'unit': 'degC'})])
    assert hs.sync(folder)['inserted'] == 1
    assert isolated_health_db.query_records(typ)[0]['value'] == -0.5
    root = ET.Element('HealthData')
    root.append(xml_node('quantity', type=typ, value='-0.5', unit='degC',
                         startDate='2026-10-23 08:00:00 +0800', endDate='2026-10-23 08:00:00 +0800'))
    source = tmp_path / 'synthetic.xml'
    ET.ElementTree(root).write(source, encoding='utf-8')
    monkeypatch.setattr(isolated_health_db, 'EXPORT_XML', source)
    assert isolated_health_db.import_all() == 1
    assert [r['value'] for r in isolated_health_db.query_records(typ)] == [-0.5, -0.5]


def test_legacy_inverted_interval_has_no_confirmed_workout_totals():
    row = dict(start_date='2026-10-23 09:00:00 +0800', end_date='2026-10-23 08:00:00 +0800',
               duration=30, total_distance=1, total_energy_burned=100)
    assert health._health_workout_rows([row]) == [dict(row, duration=None, total_distance=None, total_energy_burned=None)]
    assert row['duration'] == 30
