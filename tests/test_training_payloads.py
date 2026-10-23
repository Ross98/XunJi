import asyncio
from copy import deepcopy

import pytest
from starlette.requests import Request

from analysis import summarize_training
from data_service import extract_trains, extract_body_records
from routes import training

GOOD = {'datestr': '2026-10-22', 'movements': [{'name': '卧推', 'sets': [{'weight': 50, 'reps': 10}]}]}


@pytest.mark.parametrize('movements', [None, 1, 'bad', {}, [None, 1, {'name': ['bad'], 'type': 1, 'sets': None}]])
def test_invalid_movement_structure_does_not_hide_valid_training(movements, monkeypatch, tmp_cache):
    payload = {'trains': [{'datestr': '2026-10-22', 'movements': movements}, GOOD]}
    original = deepcopy(payload)

    class Service:
        async def get_training(self, *args, **kwargs):
            return payload

    monkeypatch.setattr(training, 'get_data_service', Service)
    monkeypatch.setattr(training, 'get_cache', lambda: tmp_cache)
    response = asyncio.run(training.training_detail_fragment(
        Request({'type': 'http', 'path': '/training/2026-10-22', 'headers': []}), '2026-10-22'))
    assert response.status_code == 200
    assert '50' in response.body.decode()
    assert summarize_training(extract_trains(payload))['total_volume'] == 500
    assert payload == original


def test_mixed_record_list_preserves_valid_rows_without_altering_raw_response():
    payload = {'trains': [None, GOOD, 'bad']}
    assert extract_trains(payload) == [GOOD]
    assert len(payload['trains']) == 3
    body = {'records': [None, {'datestr': '2026-10-22', 'type': 'weight', 'value': 70}]}
    assert extract_body_records(body) == [body['records'][1]]


def test_invalid_explicit_training_date_is_not_used_as_calendar_identity():
    payload = {'trains': [{'datestr': ['bad'], 'movements': []}, GOOD]}
    assert extract_trains(payload) == [GOOD]


def test_nested_bad_sets_are_skipped_and_valid_child_measurements_survive():
    payload = {'trains': [{'datestr': '2026-10-22', 'movements': [{'name': '卧推', 'sets': [
        None, 1, {'items': [None, {'set': None}, {'set': {'weight': 50, 'reps': 10}}]},
    ]}]}]}
    original = deepcopy(payload)
    result = extract_trains(payload)
    assert summarize_training(result)['total_volume'] == 500
    assert summarize_training(result)['total_sets'] == 1
    assert payload == original


@pytest.mark.parametrize('day', [None, 42, [], '2026-13-01', '20261022', '2026-W43-4'])
def test_training_calendar_identity_requires_canonical_date(day):
    assert extract_trains({'trains': [{'datestr': day}, GOOD]}) == [GOOD]


def test_bad_nested_refresh_does_not_replace_good_cache_or_mark_success(tmp_cache):
    from data_service import DataService
    key = 'training:2026-10-22:True'
    old = {'trains': [GOOD]}
    tmp_cache.set(key, old)
    before = tmp_cache.get_mtime(key)

    class Client:
        async def fetch_training(self, *args):
            return {'trains': [{'datestr': '2026-10-22', 'movements': None}]}

    result = asyncio.run(DataService(tmp_cache, Client()).get_training('2026-10-22', True, force_refresh=True))
    assert result == old
    assert tmp_cache.get(key) == old
    assert tmp_cache.get_mtime(key) == before
    assert tmp_cache.get('_xunji_refresh_time') is None


def test_nested_view_copies_keep_completed_flags_rpe_notes_and_source_unchanged():
    payload = {'trains': [{'datestr': '2026-10-22', 'note': {'text': '保留'}, 'movements': [
        {'name': '卧推', 'difficulty': 'hard', 'sets': [{'items': [
            {'set': {'done': False, 'weight': 50, 'reps': 10, 'rpe': '8.5'}}]}]},
    ]}]}
    original = deepcopy(payload)
    result = extract_trains(payload)
    assert result == payload['trains']
    result[0]['movements'][0]['sets'][0]['items'][0]['set']['reps'] = 12
    assert payload == original


def test_valid_envelope_wins_over_malformed_alternative():
    from data_service import DataService
    payload = {'res': {'trains': [None]}, 'trains': [GOOD]}
    assert DataService._is_valid_training_response(payload)
    assert extract_trains(payload) == [GOOD]
    body_record = {'datestr': '2026-10-22', 'type': 'weight', 'value': 70}
    body = {'res': {'records': [None]}, 'records': [body_record]}
    assert DataService._is_valid_body_response(body)
    assert extract_body_records(body) == [body_record]


def test_legacy_partial_cache_keeps_usable_rows_and_remains_eligible_for_repair(tmp_cache, monkeypatch):
    from datetime import datetime, timezone
    from data_service import DataService, get_cached_training
    payload = {'trains': [GOOD, {'datestr': '2026-10-22', 'movements': None}]}
    key = 'training:2026-10-22:True'
    tmp_cache.set(key, payload)
    monkeypatch.setattr(training, 'get_cache', lambda: tmp_cache)
    assert '2026-10-22' in training._get_cached_training_dates()
    assert summarize_training(extract_trains(get_cached_training(tmp_cache, '2026-10-22')))['total_volume'] == 500
    assert not training._should_skip_training_backfill(
        tmp_cache, '2026-10-22', datetime(2026, 10, 23, tzinfo=timezone.utc))

    class Client:
        calls = 0

        async def fetch_training(self, *args):
            self.calls += 1
            raise RuntimeError('offline')

    client = Client()
    result = asyncio.run(DataService(tmp_cache, client).get_training('2026-10-22', True))
    assert client.calls == 1
    assert summarize_training(extract_trains(result))['total_volume'] == 500
    assert tmp_cache.get(key) == payload
    assert tmp_cache.get('_xunji_refresh_time') is None
