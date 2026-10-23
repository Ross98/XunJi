"""Overly deep persisted JSON remains recoverable without rewriting preferences."""

import asyncio

import pytest

from data_service import DataService
from planner import DEFAULT_PLAN_PREFERENCES
from routes import body, plan

DEEP_ARRAY = '[' * 1500 + '0' + ']' * 1500


def test_deep_cache_json_is_a_miss_and_can_be_replaced(tmp_cache):
    raw = '{"nested":' + DEEP_ARRAY + '}'
    with tmp_cache._conn() as conn:
        conn.execute('INSERT INTO cache VALUES (?,?,?)', ('training:bad', raw, '2026-10-22'))
    assert tmp_cache.get('training:bad') is None
    with tmp_cache._conn() as conn:
        assert conn.execute('SELECT data FROM cache WHERE key=?', ('training:bad',)).fetchone()[0] == raw
    tmp_cache.set('training:bad', {'trains': []})
    assert tmp_cache.get('training:bad') == {'trains': []}


def test_bad_cache_mode_cannot_block_valid_training_mode(tmp_cache):
    day = '2026-10-22'
    good = {'trains': [{'datestr': day, 'movements': []}]}
    tmp_cache.set(f'training:{day}:True', good)
    with tmp_cache._conn() as conn:
        conn.execute('INSERT INTO cache VALUES (?,?,?)',
            (f'training:{day}:False', '{"nested":'+DEEP_ARRAY+'}', '2026-10-22'))

    class Client:
        async def fetch_training(self, *args):
            raise AssertionError('valid alternate cache should avoid upstream call')

    assert asyncio.run(DataService(tmp_cache, Client()).get_training(day)) == good


def test_deep_encoded_body_cache_is_replaced_after_successful_fetch(tmp_cache):
    key = 'body:all:2026-10-01:2026-10-22:500'
    tmp_cache.set(key, {'res': {'records': DEEP_ARRAY}})
    fresh = {'records': [{'datestr': '2026-10-22', 'type': 'weight', 'value': 70}]}
    calls = []

    class Client:
        async def query_body(self, *args, **kwargs):
            calls.append('read')
            return fresh

    result = asyncio.run(DataService(tmp_cache, Client()).get_body('2026-10-01', '2026-10-22'))
    assert result == fresh
    assert calls == ['read']
    assert tmp_cache.get(key) == fresh
    assert tmp_cache.get('_xunji_refresh_time')


@pytest.mark.parametrize('kind', ['body', 'plan'])
def test_deep_preference_file_falls_back_without_overwriting(tmp_path, monkeypatch, kind):
    path = tmp_path / (kind + '.json')
    raw = '{"nested":' + DEEP_ARRAY + '}'
    path.write_text(raw)
    if kind == 'body':
        monkeypatch.setattr(body, 'BODY_CONFIG_PATH', str(path))
        assert body._load_body_config()['height_cm'] == 170
    else:
        monkeypatch.setattr(plan, 'PLAN_CONFIG_PATH', path)
        assert plan._load_plan_preferences() == DEFAULT_PLAN_PREFERENCES
    assert path.read_text() == raw


def test_deep_bad_envelope_does_not_hide_valid_alternative_records():
    from data_service import extract_body_records
    good = [{'datestr': '2026-10-22', 'type': 'weight', 'value': 70}]
    payload = {'res': {'records': DEEP_ARRAY}, 'records': good}
    assert extract_body_records(payload) == good
    assert DataService._is_valid_body_response(payload)
    assert payload['res']['records'] == DEEP_ARRAY
