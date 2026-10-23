"""Batch identity must survive preserved timestamps and atomic cloud replacement."""
import json
import os
from pathlib import Path

import pytest

import hozz_sync as hs
from test_hozz import batch, sample


def replacement(path, identifier, *, atomic):
    previous = path.stat()
    content = json.dumps(sample(identifier)) + '\n'
    assert len(content.encode()) == previous.st_size
    if atomic:
        other = path.with_suffix('.incoming')
        other.write_text(content)
        os.utime(other, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        os.replace(other, path)
    else:
        path.write_text(content)
        os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert (path.stat().st_size, path.stat().st_mtime_ns) == (previous.st_size, previous.st_mtime_ns)


@pytest.mark.parametrize('atomic', [False, True])
def test_replaced_batch_with_same_size_and_mtime_is_not_skipped(isolated_health_db, tmp_path, atomic):
    folder = tmp_path / 'batches'
    path = batch(folder, 'a.ndjson', [sample('old')])
    assert hs.sync(folder)['new_samples'] == 1
    replacement(path, 'new', atomic=atomic)
    result = hs.sync(folder)
    assert result['status'] == 'ok'
    assert result['new_samples'] == 1
    with isolated_health_db._conn() as conn:
        assert {row[0] for row in conn.execute('SELECT id FROM hozz_samples')} == {'old', 'new'}
    assert hs.sync(folder)['files'] == 0


@pytest.mark.parametrize('atomic', [False, True])
def test_replacement_during_import_rolls_back_then_retries(isolated_health_db, tmp_path, monkeypatch, tmp_cache, atomic):
    folder = tmp_path / 'batches'
    path = batch(folder, 'a.ndjson', [sample('old')])
    original = hs._ingest
    changed = False

    def change_after_read(conn, record, counts):
        nonlocal changed
        original(conn, record, counts)
        if not changed:
            changed = True
            replacement(path, 'new', atomic=atomic)
    monkeypatch.setattr(hs, '_ingest', change_after_read)
    result = hs.sync(folder, cache=tmp_cache)
    assert result['status'] == 'partial'
    assert result['files'] == result['new_samples'] == 0
    assert tmp_cache.get('_health_import_time') is None
    with isolated_health_db._conn() as conn:
        assert conn.execute('SELECT count(*) FROM hozz_samples').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM hozz_files').fetchone()[0] == 0
    monkeypatch.setattr(hs, '_ingest', original)
    assert hs.sync(folder)['new_samples'] == 1
    with isolated_health_db._conn() as conn:
        assert [row[0] for row in conn.execute('SELECT id FROM hozz_samples')] == ['new']


def test_replacement_between_stat_and_open_is_retried(isolated_health_db, tmp_path, monkeypatch):
    folder = tmp_path / 'batches'
    path = batch(folder, 'a.ndjson', [sample('old')])
    original = Path.open
    changed = False

    def replacing_open(target, *args, **kwargs):
        nonlocal changed
        if target == path and not changed:
            changed = True
            replacement(path, 'new', atomic=True)
        return original(target, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', replacing_open)
    assert hs.sync(folder)['status'] == 'partial'
    assert hs.sync(folder)['new_samples'] == 1


def test_legacy_file_signature_is_rechecked_once_without_duplicate_data(isolated_health_db, tmp_path):
    folder = tmp_path / 'batches'
    path = batch(folder, 'a.ndjson', [sample('old')])
    assert hs.sync(folder)['new_samples'] == 1
    info = path.stat()
    with isolated_health_db._conn() as conn:
        conn.execute('UPDATE hozz_files SET signature=? WHERE path=?',
                     (f'{info.st_size}:{info.st_mtime_ns}', str(path)))
    result = hs.sync(folder)
    assert result['files'] == 1
    assert result['new_samples'] == result['inserted'] == 0
    assert hs.sync(folder)['files'] == 0
    with isolated_health_db._conn() as conn:
        assert conn.execute('SELECT count(*) FROM hozz_samples').fetchone()[0] == 1


def test_unchanged_batch_is_skipped_without_opening_contents(isolated_health_db, tmp_path, monkeypatch):
    folder = tmp_path / 'batches'
    path = batch(folder, 'a.ndjson', [sample('old')])
    assert hs.sync(folder)['new_samples'] == 1
    original = Path.open

    def forbidden(target, *args, **kwargs):
        if target == path:
            raise AssertionError('unchanged batch content must not be reread')
        return original(target, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', forbidden)
    result = hs.sync(folder)
    assert result['status'] == 'ok' and result['files'] == 0


def test_replacement_after_descriptor_check_is_caught_by_path_check(isolated_health_db, tmp_path, monkeypatch):
    folder = tmp_path / 'batches'
    path = batch(folder, 'a.ndjson', [sample('old')])
    original = Path.stat
    calls = 0

    def replacing_stat(target, *args, **kwargs):
        nonlocal calls
        if target == path:
            calls += 1
            if calls == 2:
                replacement(path, 'new', atomic=True)
        return original(target, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', replacing_stat)
    result = hs.sync(folder)
    assert result['status'] == 'partial' and result['files'] == 0
    with isolated_health_db._conn() as conn:
        assert conn.execute('SELECT count(*) FROM hozz_samples').fetchone()[0] == 0
    assert hs.sync(folder)['new_samples'] == 1


def test_legacy_recheck_keeps_data_refresh_marker(isolated_health_db, tmp_path, tmp_cache):
    folder = tmp_path / 'batches'
    path = batch(folder, 'a.ndjson', [sample('old')])
    assert hs.sync(folder, cache=tmp_cache)['inserted'] == 1
    marker = tmp_cache.get('_health_import_time')
    info = path.stat()
    with isolated_health_db._conn() as conn:
        conn.execute('UPDATE hozz_files SET signature=? WHERE path=?',
                     (f'{info.st_size}:{info.st_mtime_ns}', str(path)))
    assert hs.sync(folder, cache=tmp_cache)['files'] == 1
    assert tmp_cache.get('_health_import_time') == marker
