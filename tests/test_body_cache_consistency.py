"""Overlapping body views must respect newer complete snapshots, including deletions."""
import asyncio
from copy import deepcopy

import pytest

from data_service import DataService, extract_body_records

END = '2026-10-23'
FULL = f'body:all:None:{END}:500'
WINDOW = f'body:all:2026-09-24:{END}:500'
OLD = [
    {'datestr': '2026-09-01', 'type': 'weight', 'value': 69},
    {'datestr': '2026-10-01', 'type': 'weight', 'value': 70},
    {'datestr': '2026-10-01', 'type': 'bodyfat', 'value': 20},
    {'datestr': END, 'type': 'weight', 'value': 71},
]


class Offline:
    calls = 0

    async def query_body(self, *args, **kwargs):
        self.calls += 1
        raise RuntimeError('offline')


def save(cache, key, rows, version, **extra):
    cache.set(key, {'records': deepcopy(rows), **extra})
    with cache._conn() as conn:
        conn.execute('UPDATE cache SET mtime=? WHERE key=?', (version, key))


def rows(data):
    return sorted(extract_body_records(data), key=lambda row: (row['datestr'], row['type']))


@pytest.mark.parametrize('force', [False, True])
def test_newer_empty_window_removes_only_its_scope_from_full_view(tmp_cache, force):
    save(tmp_cache, FULL, OLD, '2026-10-22T00:00:00+00:00')
    save(tmp_cache, WINDOW, [], '2026-10-23T00:00:00+00:00')
    marker = {'time': '2026-10-22T00:00:00+00:00'}
    tmp_cache.set('_xunji_refresh_time', marker)
    client = Offline()
    result = asyncio.run(DataService(tmp_cache, client).get_body(None, END, force_refresh=force))
    assert rows(result) == OLD[:1]
    assert DataService.body_history_complete(result)
    assert client.calls == int(force)
    assert tmp_cache.get(FULL) == {'records': OLD}
    assert tmp_cache.get(WINDOW) == {'records': []}
    assert tmp_cache.get('_xunji_refresh_time') == marker


def test_newer_full_snapshot_corrects_existing_short_window(tmp_cache):
    save(tmp_cache, WINDOW, OLD[1:], '2026-10-22T00:00:00+00:00')
    fresh = [{'datestr': END, 'type': 'weight', 'value': 72}]
    save(tmp_cache, FULL, OLD[:1] + fresh, '2026-10-23T00:00:00+00:00')
    result = asyncio.run(DataService(tmp_cache, Offline()).get_body('2026-09-24', END))
    assert rows(result) == fresh


def test_partial_date_and_type_overlays_apply_in_version_order(tmp_cache):
    save(tmp_cache, FULL, OLD, '2026-10-20T00:00:00+00:00')
    save(tmp_cache, 'body:weight:2026-10-01:2026-10-10:500', [], '2026-10-22T00:00:00+00:00')
    fresh = {'datestr': '2026-10-01', 'type': 'weight', 'value': 72}
    save(tmp_cache, 'body:weight:2026-09-30:2026-10-02:2', [fresh], '2026-10-21T20:00:00-05:00')
    result = asyncio.run(DataService(tmp_cache, Offline()).get_body(None, END))
    assert rows(result) == sorted([OLD[0], OLD[2], OLD[3], fresh], key=lambda r: (r['datestr'], r['type']))


@pytest.mark.parametrize('version,extra', [
    ('2026-10-21T00:00:00+00:00', {}),
    ('invalid', {}),
    ('2026-10-22T00:00:00+00:00', {}),  # equal versions cannot establish recency
    ('2026-10-23T00:00:00+00:00', {'_cache_fallback': {'saved_at': None}}),
])
def test_older_unknown_or_fallback_snapshot_cannot_delete_records(tmp_cache, version, extra):
    save(tmp_cache, FULL, OLD, '2026-10-22T00:00:00+00:00')
    save(tmp_cache, WINDOW, [], version, **extra)
    result = asyncio.run(DataService(tmp_cache, Offline()).get_body(None, END))
    assert rows(result) == rows({'records': OLD})


def test_truncated_or_nonoverlapping_snapshots_cannot_replace_scope(tmp_cache):
    save(tmp_cache, FULL, OLD, '2026-10-22T00:00:00+00:00')
    save(tmp_cache, f'body:all:2026-10-01:{END}:1', [OLD[-1]], '2026-10-23T00:00:00+00:00')
    save(tmp_cache, 'body:all:2025-01-01:2025-12-31:500', [], '2026-10-23T00:00:00+00:00')
    save(tmp_cache, f'body:height:None:{END}:500', [], '2026-10-23T00:00:00+00:00')
    assert rows(asyncio.run(DataService(tmp_cache, Offline()).get_body(None, END))) == rows({'records': OLD})


def test_snapshot_failure_fallback_also_respects_newer_partial_scope(tmp_cache):
    save(tmp_cache, FULL, OLD, '2026-10-22T00:00:00+00:00')
    save(tmp_cache, 'body:weight:2026-10-01:2026-10-10:500', [], '2026-10-23T00:00:00+00:00')
    result = asyncio.run(DataService(tmp_cache, Offline()).get_body('2026-09-15', END))
    assert rows(result) == rows({'records': OLD[2:]})
    assert result['_cache_fallback']


def test_dashboard_cached_path_uses_same_reconciled_view(tmp_cache):
    from routes.dashboard import _get_body_records

    save(tmp_cache, WINDOW, OLD[1:], '2026-10-22T00:00:00+00:00')
    save(tmp_cache, FULL, [], '2026-10-23T00:00:00+00:00')
    assert asyncio.run(_get_body_records(Offline(), tmp_cache, WINDOW, '2026-09-24', END)) == []


def test_newer_snapshot_arriving_during_failed_refresh_is_used(tmp_cache):
    save(tmp_cache, FULL, OLD, '2026-10-22T00:00:00+00:00')

    class Refresh(Offline):
        async def query_body(self, *args, **kwargs):
            save(tmp_cache, WINDOW, [], '2026-10-23T00:00:00+00:00')
            raise RuntimeError('offline')

    result = asyncio.run(DataService(tmp_cache, Refresh()).get_body(None, END, force_refresh=True))
    assert rows(result) == OLD[:1]


def test_overlay_filters_source_records_outside_both_query_scopes(tmp_cache):
    save(tmp_cache, WINDOW, OLD[1:], '2026-10-22T00:00:00+00:00')
    fresh = {'datestr': END, 'type': 'weight', 'value': 72}
    save(tmp_cache, f'body:weight:2026-10-01:{END}:500',
         [OLD[0], OLD[2], fresh], '2026-10-23T00:00:00+00:00')
    result = asyncio.run(DataService(tmp_cache, Offline()).get_body('2026-09-24', END))
    assert rows(result) == [OLD[2], fresh]


def test_unrelated_or_older_payloads_are_not_decoded_on_cache_hit(tmp_cache, monkeypatch):
    save(tmp_cache, WINDOW, OLD[1:], '2026-10-22T00:00:00+00:00')
    save(tmp_cache, FULL, [], '2026-10-21T00:00:00+00:00')
    save(tmp_cache, 'body:all:2025-01-01:2025-12-31:500', [], '2026-10-23T00:00:00+00:00')
    read = tmp_cache.get_with_mtime
    calls = []

    def tracked(key):
        calls.append(key)
        return read(key)

    monkeypatch.setattr(tmp_cache, 'get_with_mtime', tracked)
    asyncio.run(DataService(tmp_cache, Offline()).get_body('2026-09-24', END))
    assert calls == [WINDOW]


def test_replaced_candidate_uses_its_atomic_payload_version(tmp_cache, monkeypatch):
    from data_service import get_cached_body

    save(tmp_cache, FULL, OLD, '2026-10-22T00:00:00+00:00')
    save(tmp_cache, WINDOW, [], '2026-10-23T00:00:00+00:00')
    read = tmp_cache.get_with_mtime

    def replaced(key):
        if key == WINDOW:
            save(tmp_cache, WINDOW, [], '2026-10-21T00:00:00+00:00')
        return read(key)

    monkeypatch.setattr(tmp_cache, 'get_with_mtime', replaced)
    assert rows(get_cached_body(tmp_cache, FULL)) == rows({'records': OLD})


def test_clear_during_overlay_does_not_return_a_composed_old_view(tmp_cache, monkeypatch):
    from data_service import get_cached_body

    save(tmp_cache, FULL, OLD, '2026-10-22T00:00:00+00:00')
    save(tmp_cache, WINDOW, [], '2026-10-23T00:00:00+00:00')
    read = tmp_cache.get_with_mtime

    def cleared(key):
        data = read(key)
        if key == WINDOW:
            tmp_cache.clear()
        return data

    monkeypatch.setattr(tmp_cache, 'get_with_mtime', cleared)
    assert get_cached_body(tmp_cache, FULL) is None
