"""Canonical training modes must share one SQLite content/version snapshot."""
from contextlib import contextmanager
import pytest
from cache import Cache
from data_service import get_cached_training

DAY = '2026-10-22'
FULL = f'training:{DAY}:True'
SUMMARY = f'training:{DAY}:False'
EMPTY = {'trains': []}
OLD = {'trains': [{'datestr': DAY, 'title': 'synthetic old', 'movements': []}]}


def seed(cache, key, value, hour):
    cache.set(key, value)
    with cache._conn() as conn:
        conn.execute('UPDATE cache SET mtime=? WHERE key=?', (f'2026-10-22T{hour:02d}:00:00Z',key))


def test_refresh_cannot_pair_old_payload_with_new_timestamp(tmp_cache, monkeypatch):
    seed(tmp_cache, FULL, OLD, 8)
    seed(tmp_cache, SUMMARY, EMPTY, 9)
    original = tmp_cache.get
    def refresh_after_payload(key):
        payload = original(key)
        if key == FULL:
            seed(tmp_cache, FULL, EMPTY, 10)
        return payload
    monkeypatch.setattr(tmp_cache, 'get', refresh_after_payload)
    assert get_cached_training(tmp_cache, DAY) == EMPTY


def test_clear_between_mode_reads_cannot_resurrect_deleted_records(tmp_cache, monkeypatch):
    seed(tmp_cache, FULL, OLD, 8)
    seed(tmp_cache, SUMMARY, EMPTY, 9)
    writer = Cache(tmp_cache._db_path)
    original = tmp_cache._conn
    changed = []
    def clear_after_snapshot():
        if not changed:
            changed.append(True)
            writer.clear()
            seed(writer, SUMMARY, EMPTY, 10)
    class Cursor:
        def __init__(self, cursor): self.cursor = cursor
        def fetchone(self):
            row = self.cursor.fetchone()
            clear_after_snapshot()
            return row
        def fetchall(self):
            rows = self.cursor.fetchall()
            clear_after_snapshot()
            return rows
    class Connection:
        def __init__(self, conn): self.conn = conn
        def execute(self, sql, params=()):
            cursor = self.conn.execute(sql,params)
            return Cursor(cursor) if sql.startswith('SELECT') and 'data' in sql else cursor
    @contextmanager
    def concurrent_read():
        with original() as conn:
            yield Connection(conn)
    monkeypatch.setattr(tmp_cache, '_conn', concurrent_read)
    assert get_cached_training(tmp_cache, DAY) == EMPTY
    assert changed == [True]
    assert writer.get(FULL) is None
    assert writer.get(SUMMARY) == EMPTY


def test_canonical_modes_require_one_select(tmp_cache, monkeypatch):
    seed(tmp_cache, FULL, OLD, 8)
    seed(tmp_cache, SUMMARY, EMPTY, 9)
    original = tmp_cache._conn
    selects = []
    @contextmanager
    def traced():
        with original() as conn:
            conn.set_trace_callback(lambda sql: selects.append(sql) if sql.startswith('SELECT') else None)
            yield conn
    monkeypatch.setattr(tmp_cache, '_conn', traced)
    assert get_cached_training(tmp_cache, DAY) == EMPTY
    assert len(selects) == 1


@pytest.mark.parametrize('bad', ['{broken', '[]', 'null', '{"deep":'+ '['*1500+'0'+']'*1500+'}'])
def test_corrupt_batch_neighbor_preserves_valid_snapshot(tmp_cache, bad):
    seed(tmp_cache, SUMMARY, EMPTY, 9)
    with tmp_cache._conn() as conn:
        conn.execute('INSERT INTO cache VALUES (?,?,?)',(FULL,bad,'2026-10-22T10:00:00Z'))
    assert get_cached_training(tmp_cache, DAY) == EMPTY


def test_batch_read_limits_rows_to_literal_requested_keys(tmp_cache):
    key = "training:' OR 1=1 --"
    tmp_cache.set(key, OLD)
    tmp_cache.set('unrelated', {'private': 'synthetic'})
    timestamp = tmp_cache.get_mtime(key)
    assert tmp_cache.get_many_with_mtime([key, key, 'missing']) == {key: (OLD, timestamp)}
    assert tmp_cache.get_many_with_mtime([]) == {}


def test_atomic_single_row_adapter_remains_usable():
    class Adapter:
        def get_with_mtime(self, key):
            return (OLD, '2026-10-22T08:00:00Z') if key == FULL else (EMPTY, '2026-10-22T09:00:00Z')
        def get(self, key):
            raise AssertionError('Do not split an available atomic content/version read')
    assert get_cached_training(Adapter(), DAY) == EMPTY
