"""Hozz status must describe confirmed coverage and survive damaged metadata."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import hozz_sync as hs
from test_hozz import batch, sample


def coverage(**changes):
    row = dict(schemaVersion=1, kind='typeCoverage', type='HeartRate',
               complete=False, state='draining', observedAt='2026-10-23T01:00:00Z')
    return {**row, **changes}


def stored_state(ah, value):
    hs._initialize()
    raw = value if isinstance(value, str) else json.dumps(value)
    with ah._conn() as conn:
        conn.execute('INSERT OR REPLACE INTO hozz_sync_state VALUES (1,?)', (raw,))
    return raw


def stored_coverage(ah, row, raw=None):
    hs._initialize()
    with ah._conn() as conn:
        conn.execute('INSERT OR REPLACE INTO hozz_coverage VALUES (?,?,?)',
                     (row['type'], row['observedAt'], raw if raw is not None else json.dumps(row)))


@pytest.mark.parametrize('fields', [
    {'complete': True, 'state': 'draining'},
    {'complete': True, 'state': 'authorizationIndeterminate'},
    {'complete': 'false'}, {'complete': 1}, {'complete': []},
])
def test_contradictory_or_mistyped_coverage_is_one_uncertain_type(isolated_health_db, fields):
    stored_coverage(isolated_health_db, coverage(**fields))
    result = hs.status()
    assert (result['complete'], result['incomplete'], result['uncertain']) == (0, 0, 1)
    assert result['coverage'][0]['coverage_status'] == 'uncertain'


@pytest.mark.parametrize('fields,expected', [
    ({'complete': True, 'state': 'anchorClosed'}, 'complete'),
    ({'complete': False, 'state': 'draining'}, 'incomplete'),
    ({'complete': False, 'state': 'authorizationIndeterminate'}, 'uncertain'),
    ({'complete': False, 'state': 'futureUnknownState'}, 'uncertain'),
])
def test_normal_coverage_classification_stays_consistent(isolated_health_db, fields, expected):
    stored_coverage(isolated_health_db, coverage(**fields))
    result = hs.status()
    assert sum(result[key] for key in ('complete', 'incomplete', 'uncertain')) == 1
    assert result[expected] == 1
    assert result['coverage'][0]['coverage_status'] == expected


def test_future_check_time_does_not_claim_current_sync_health(isolated_health_db):
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    stored_state(isolated_health_db, {'status': 'ok', 'checked_at': future})
    result = hs.status()
    assert result['check_stale'] is True
    assert result['checked_at'] == future


@pytest.mark.parametrize('raw', ['{broken', '[]', 'null', '[' * 1500 + '0' + ']' * 1500],
                         ids=['broken', 'array', 'null', 'deep'])
def test_damaged_status_does_not_block_page_or_next_sync(isolated_health_db, tmp_path, raw):
    stored_state(isolated_health_db, raw)
    result = hs.status()
    assert result['check_stale'] is True
    folder = tmp_path / 'batches'
    batch(folder, 'valid.ndjson', [sample()])
    assert hs.sync(folder)['status'] == 'ok'
    assert hs.status()['samples'] == 1


@pytest.mark.parametrize('value', ['invalid-date', True, [], {}, '9999-12-31T23:00:00Z'])
def test_invalid_last_sync_timestamp_does_not_crash_status(isolated_health_db, value):
    raw = stored_state(isolated_health_db, {'last_sync': value})
    assert hs.status()['last_sync_bj'] is None
    with isolated_health_db._conn() as conn:
        assert conn.execute('SELECT raw FROM hozz_sync_state WHERE id=1').fetchone()[0] == raw


@pytest.mark.parametrize('version', [True, False, 1.0, '1'])
def test_invalid_schema_header_rolls_back_and_other_files_continue(isolated_health_db, tmp_path, version):
    folder = tmp_path / 'batches'
    bad = batch(folder, 'a.ndjson', [sample('rolled-back'), sample('invalid', schemaVersion=version)])
    batch(folder, 'b.ndjson', [sample('valid')])
    result = hs.sync(folder)
    assert result['status'] == 'partial'
    assert result['new_samples'] == 1
    with isolated_health_db._conn() as conn:
        assert [r[0] for r in conn.execute('SELECT id FROM hozz_samples')] == ['valid']
        assert conn.execute('SELECT 1 FROM hozz_files WHERE path=?', (str(bad),)).fetchone() is None


@pytest.mark.parametrize('fields', [{'type': ''}, {'type': None}, {'type': 17}, {'type': []},
                                     {'complete': 'false'}, {'complete': 1}, {'state': []}])
def test_invalid_coverage_fields_are_retryable_batch_errors(isolated_health_db, tmp_path, fields):
    folder = tmp_path / 'batches'
    batch(folder, 'a.ndjson', [sample('rolled-back'), coverage(**fields)])
    batch(folder, 'b.ndjson', [sample('valid')])
    result = hs.sync(folder)
    assert result['status'] == 'partial'
    assert result['new_samples'] == 1
    assert hs.status()['coverage'] == []


@pytest.mark.parametrize('raw', ['{broken', '[]', 'null', '{"complete": NaN}', '[' * 1500 + '0' + ']' * 1500],
                         ids=['broken', 'array', 'null', 'nonfinite', 'deep'])
def test_corrupt_coverage_remains_visible_as_unknown_without_rewriting(isolated_health_db, raw):
    stored_coverage(isolated_health_db, coverage(), raw=raw)
    result = hs.status()
    assert result['uncertain'] == 1
    assert result['coverage'][0]['type'] == 'HeartRate'
    assert result['coverage'][0]['coverage_status'] == 'uncertain'
    with isolated_health_db._conn() as conn:
        assert conn.execute('SELECT raw FROM hozz_coverage').fetchone()[0] == raw


def test_bad_persisted_fields_cannot_override_live_derived_status(isolated_health_db):
    stored_state(isolated_health_db, {
        'status': {}, 'folder': [], 'checked_at': True, 'last_sync': {},
        'errors': True, 'inserted': False, 'check_stale': False,
        'checked_at_bj': 'invented current check', 'last_sync_bj': 'invented receipt',
        'samples': 999, 'coverage': [{'type': 'invented', 'complete': True}],
    })
    result = hs.status()
    assert result['status'] == 'idle'
    assert result['check_stale'] is True
    assert result['checked_at_bj'] is result['last_sync_bj'] is None
    assert result['coverage'] == []
    assert result['samples'] == result['inserted'] == 0


def test_bad_previous_coverage_timestamp_does_not_block_new_valid_coverage(isolated_health_db, tmp_path):
    stored_coverage(isolated_health_db, coverage())
    with isolated_health_db._conn() as conn:
        conn.execute("UPDATE hozz_coverage SET observed_at='bad-date'")
    folder = tmp_path / 'batches'
    batch(folder, 'a.ndjson', [coverage(complete=True, state='anchorClosed')])
    assert hs.sync(folder)['status'] == 'ok'
    assert hs.status()['complete'] == 1


@pytest.mark.parametrize('legacy_template_context', [False, True])
def test_health_html_summary_and_coverage_rows_agree(
    isolated_health_db, tmp_cache, tmp_path, monkeypatch, legacy_template_context
):
    import asyncio
    from starlette.requests import Request
    from routes import health

    stored_coverage(isolated_health_db, coverage(type='SyntheticConflict', complete=True))
    stored_coverage(isolated_health_db, coverage(type='SyntheticString', complete='false'))
    stored_coverage(isolated_health_db, coverage(type='SyntheticComplete', complete=True, state='anchorClosed'))
    stored_coverage(isolated_health_db, coverage(type='SyntheticDraining'))
    monkeypatch.setattr(health, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(hs, 'source_folder', lambda: tmp_path / 'synthetic')
    monkeypatch.setattr(isolated_health_db, 'EXPORT_XML', tmp_path / 'absent.xml')
    if legacy_template_context:
        status = hs.status()
        for item in status['coverage']:
            item.pop('coverage_status')
        monkeypatch.setattr(health, '_hozz_status_context', lambda: status)
    response = asyncio.run(health.health_page(Request({'type': 'http', 'path': '/health', 'headers': []})))
    html = response.body.decode()
    assert response.status_code == 200
    assert '类型已完成 1 · 同步中 1 · 权限或覆盖未确认 2' in html
    assert '<td>已完成（anchorClosed）</td>' in html
    assert '<td>同步中（draining）</td>' in html
    assert html.count('<td>权限或覆盖未确认（draining）</td>') == 2


@pytest.mark.parametrize('invalid_kind', ['state', 'coverage'])
def test_status_http_survives_corrupt_metadata_and_does_not_rewrite_it(
    isolated_health_db, tmp_cache, tmp_path, monkeypatch, invalid_kind
):
    import asyncio
    import httpx
    from fastapi import FastAPI
    from routes import health

    if invalid_kind == 'state':
        stored_state(isolated_health_db, '{broken')
    else:
        stored_coverage(isolated_health_db, coverage(), raw='{broken')
    monkeypatch.setattr(health, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(hs, 'source_folder', lambda: tmp_path / 'synthetic')
    monkeypatch.setattr(isolated_health_db, 'EXPORT_XML', tmp_path / 'absent.xml')
    app = FastAPI()
    app.include_router(health.router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
            response = await client.get('/health/sync/status')
            assert response.status_code == 200
            assert response.json()['watcher_running'] is False
            page = await client.get('/health')
            assert page.status_code == 200
            assert '检查状态待重新确认' in page.text
        table = 'hozz_sync_state' if invalid_kind == 'state' else 'hozz_coverage'
        with isolated_health_db._conn() as conn:
            assert conn.execute(f'SELECT raw FROM {table}').fetchone()[0] == '{broken'

    asyncio.run(run())


@pytest.mark.parametrize('identifier', [None, '', ' '])
def test_invalid_legacy_coverage_key_is_unknown_and_renderable(isolated_health_db, identifier):
    from routes.health import health_label
    stored_coverage(isolated_health_db, coverage(type=identifier, complete=True, state='anchorClosed'))
    result = hs.status()
    assert (result['complete'], result['incomplete'], result['uncertain']) == (0, 0, 1)
    assert health_label(result['coverage'][0]['type']) == '未知类型'
