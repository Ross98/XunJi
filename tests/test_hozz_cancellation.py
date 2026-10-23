"""Cancellation must stop new sync work while preserving already committed batches."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import sqlite3
import threading

import pytest

import hozz_sync as hs
from test_hozz import batch,sample


@pytest.fixture(autouse=True)
def isolate_storage(isolated_health_db):
    yield


@pytest.mark.parametrize('existing', [False, True])
def test_pre_cancelled_sync_never_initializes_or_scans(tmp_path,monkeypatch,tmp_cache,existing):
    import apple_health as ah
    path=tmp_path/'health.sqlite'
    monkeypatch.setattr(ah,'HEALTH_DB_PATH',path)
    if existing: ah._init_db()
    event=threading.Event();event.set()
    calls=[]
    def forbidden(*args,**kwargs):
        calls.append(True)
        raise AssertionError('Cancelled sync started I/O')
    monkeypatch.setattr(hs,'_initialize',forbidden)
    marker={'time':'2026-10-20T00:00:00Z'}
    tmp_cache.set('_health_import_time',marker)
    result=hs.sync(tmp_path/'missing',cache=tmp_cache,cancel_event=event)
    assert result['status']=='cancelled'
    assert result['files']==result['new_samples']==result['inserted']==0
    assert calls==[]
    assert path.exists() is existing
    assert tmp_cache.get('_health_import_time')==marker


def test_waiting_for_lock_can_cancel_without_releasing_another_owners_lock(tmp_path,monkeypatch):
    event=threading.Event()
    initialized=[]
    monkeypatch.setattr(hs,'_initialize',lambda *args,**kwargs:initialized.append(True))
    entered=threading.Event()
    base=threading.Lock()
    class ObservedLock:
        def acquire(self,*args,**kwargs):
            entered.set()
            return base.acquire(*args,**kwargs)
        def release(self): base.release()
        def locked(self): return base.locked()
        def __enter__(self): self.acquire(); return self
        def __exit__(self,*args): self.release()
    monkeypatch.setattr(hs,'SYNC_LOCK',ObservedLock())
    hs.SYNC_LOCK.acquire()
    entered.clear()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(hs.sync,tmp_path,cancel_event=event)
            assert entered.wait(1),'Sync never attempted to acquire held lock'
            event.set()
            try:
                result=future.result(timeout=.5)
                assert result['status']=='cancelled'
                assert hs.SYNC_LOCK.locked()
                assert initialized==[]
            finally:
                # Also releases a pre-fix implementation so a failure cannot hang pytest.
                if hs.SYNC_LOCK.locked(): hs.SYNC_LOCK.release()
    finally:
        if hs.SYNC_LOCK.locked(): hs.SYNC_LOCK.release()


def test_cancel_after_initialization_never_starts_discovery(isolated_health_db,tmp_path,monkeypatch):
    event=threading.Event()
    original=hs._initialize
    def initialize(*args,**kwargs):
        original(*args,**kwargs)
        event.set()
    monkeypatch.setattr(hs,'_initialize',initialize)
    result=hs.sync(tmp_path/'missing',cancel_event=event)
    assert result['status']=='cancelled'
    assert result['errors']==[]
    with isolated_health_db._conn() as conn:
        assert conn.execute('SELECT count(*) FROM hozz_sync_state').fetchone()[0]==0


def test_cancel_during_empty_discovery_is_not_success(isolated_health_db,tmp_path,monkeypatch):
    from pathlib import Path
    folder=tmp_path/'batches';folder.mkdir()
    event=threading.Event()
    glob=Path.glob
    def cancel_glob(path,pattern):
        if path==folder:
            event.set()
            return iter(())
        return glob(path,pattern)
    monkeypatch.setattr(Path,'glob',cancel_glob)
    result=hs.sync(folder,cancel_event=event)
    assert result['status']=='cancelled'
    assert result['last_sync'] is None


def test_cancellation_before_final_commit_rolls_back_whole_current_file_and_can_resume(
    isolated_health_db,tmp_path,monkeypatch,tmp_cache
):
    folder=tmp_path/'batches'
    path=batch(folder,'a.ndjson',[sample()])
    event=threading.Event()
    original=hs._ingest
    def cancel_after_ingest(*args,**kwargs):
        original(*args,**kwargs)
        event.set()
    monkeypatch.setattr(hs,'_ingest',cancel_after_ingest)
    result=hs.sync(folder,cache=tmp_cache,cancel_event=event)
    assert result['status']=='cancelled'
    assert result['files']==result['new_samples']==result['inserted']==0
    assert isolated_health_db.query_records()==[]
    assert tmp_cache.get('_health_import_time') is None
    with isolated_health_db._conn() as conn:
        assert conn.execute('SELECT count(*) FROM hozz_files WHERE path=?',(str(path),)).fetchone()[0]==0
    monkeypatch.setattr(hs,'_ingest',original)
    event.clear()
    assert hs.sync(folder,cache=tmp_cache,cancel_event=event)['inserted']==1
    assert hs.sync(folder,cache=tmp_cache,cancel_event=event)['new_samples']==0


def test_cancellation_keeps_prior_completed_files_and_their_marker(isolated_health_db,tmp_path,monkeypatch,tmp_cache):
    folder=tmp_path/'batches'
    batch(folder,'a.ndjson',[sample('committed')])
    batch(folder,'b.ndjson',[sample('rolled-back',startDate='2026-09-30T02:00:00Z',endDate='2026-09-30T02:01:00Z')])
    event=threading.Event()
    original=hs._ingest
    def cancel_second(conn,row,counts):
        original(conn,row,counts)
        if row.get('id')=='rolled-back': event.set()
    monkeypatch.setattr(hs,'_ingest',cancel_second)
    result=hs.sync(folder,cache=tmp_cache,cancel_event=event)
    assert result['status']=='cancelled'
    assert result['files']==result['new_samples']==result['inserted']==1
    assert tmp_cache.get('_health_import_time')=={'time':result['checked_at']}
    with isolated_health_db._conn() as conn:
        assert [r[0] for r in conn.execute('SELECT id FROM hozz_samples')]==['committed']


def test_backup_started_before_cancellation_finishes_without_starting_schema_migration(
    isolated_health_db,tmp_path,monkeypatch
):
    event=threading.Event()
    original=isolated_health_db._conn
    class BackupThenCancel:
        def __init__(self,conn): self.conn=conn
        def __getattr__(self,key): return getattr(self.conn,key)
        def backup(self,target):
            self.conn.backup(target)
            event.set()
    @contextmanager
    def connection():
        with original() as conn: yield BackupThenCancel(conn)
    monkeypatch.setattr(isolated_health_db,'_conn',connection)
    result=hs.sync(tmp_path/'missing',cancel_event=event)
    assert result['status']=='cancelled'
    with original() as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='hozz_samples'").fetchone() is None
    backups=list(tmp_path.glob('*.pre-hozz-*.bak'))
    assert len(backups)==1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_cancellation_after_base_tables_skips_importer_schema(isolated_health_db,tmp_path,monkeypatch):
    event=threading.Event()
    # A fresh store reaches the base-table boundary without a backup phase.
    monkeypatch.setattr(isolated_health_db,'HEALTH_DB_PATH',tmp_path/'fresh.sqlite')
    original=isolated_health_db._init_db
    def base_then_cancel():
        original()
        event.set()
    monkeypatch.setattr(isolated_health_db,'_init_db',base_then_cancel)
    result=hs.sync(tmp_path,cancel_event=event)
    assert result['status']=='cancelled'
    with isolated_health_db._conn() as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='hozz_samples'").fetchone() is None


def test_cancelled_initialization_keeps_unique_private_complete_backups(isolated_health_db,tmp_path,monkeypatch):
    from datetime import datetime
    import stat
    import os
    event=threading.Event()
    original=isolated_health_db._conn
    class FrozenClock:
        @classmethod
        def now(cls,*args,**kwargs): return datetime(2026,10,23,12)
    monkeypatch.setattr(hs,'datetime',FrozenClock)
    class BackupThenCancel:
        def __init__(self,conn): self.conn=conn
        def __getattr__(self,key): return getattr(self.conn,key)
        def backup(self,target):
            self.conn.backup(target)
            event.set()
    @contextmanager
    def connection():
        with original() as conn: yield BackupThenCancel(conn)
    monkeypatch.setattr(isolated_health_db,'_conn',connection)
    for _ in range(2):
        event.clear()
        assert hs.sync(tmp_path,cancel_event=event)['status']=='cancelled'
    backups=list(tmp_path.glob('*.pre-hozz-*.bak'))
    assert len(backups)==2
    for backup in backups:
        if os.name=='posix':
            assert stat.S_IMODE(backup.stat().st_mode)==0o600
        with sqlite3.connect(backup) as conn:
            assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_failed_backup_closes_and_removes_only_its_own_incomplete_file(isolated_health_db,tmp_path,monkeypatch):
    protected=tmp_path/(isolated_health_db.HEALTH_DB_PATH.name+'.pre-hozz-preserved.bak')
    protected.write_bytes(b'preserved backup')
    original=isolated_health_db._conn
    targets=[]
    class FailedBackup:
        def __init__(self,conn): self.conn=conn
        def __getattr__(self,key): return getattr(self.conn,key)
        def backup(self,target):
            targets.append(target)
            raise sqlite3.OperationalError('synthetic backup failure')
    @contextmanager
    def connection():
        with original() as conn: yield FailedBackup(conn)
    monkeypatch.setattr(isolated_health_db,'_conn',connection)
    with pytest.raises(sqlite3.OperationalError,match='synthetic backup failure'):
        hs._initialize()
    assert list(tmp_path.glob('*.pre-hozz-*.bak'))==[protected]
    assert protected.read_bytes()==b'preserved backup'
    with pytest.raises(sqlite3.ProgrammingError,match='closed'):
        targets[0].execute('SELECT 1')
    with original() as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='hozz_samples'").fetchone() is None


def test_cancel_after_directory_check_never_starts_glob(isolated_health_db,tmp_path,monkeypatch):
    from pathlib import Path
    event=threading.Event()
    folder=tmp_path/'batches';folder.mkdir()
    is_dir=Path.is_dir
    scanned=[]
    def cancel_directory(path):
        result=is_dir(path)
        if path==folder: event.set()
        return result
    def forbidden_glob(*args,**kwargs):
        scanned.append(True)
        raise AssertionError('Cancelled task began directory scan')
    monkeypatch.setattr(Path,'is_dir',cancel_directory)
    monkeypatch.setattr(Path,'glob',forbidden_glob)
    result=hs.sync(folder,cancel_event=event)
    assert result['status']=='cancelled'
    assert scanned==[]
    assert result['errors']==[]


def test_service_stop_joins_real_sync_waiter_before_other_owner_releases_lock(
    isolated_health_db,tmp_cache,tmp_path,monkeypatch
):
    import asyncio
    from routes import health
    entered=threading.Event()
    base=threading.Lock()
    class ObservedLock:
        def acquire(self,*args,**kwargs):
            entered.set()
            return base.acquire(*args,**kwargs)
        def release(self): base.release()
        def __enter__(self): self.acquire(); return self
        def __exit__(self,*args): self.release()
    lock=ObservedLock()
    monkeypatch.setattr(hs,'SYNC_LOCK',lock)
    monkeypatch.setattr(hs,'source_folder',lambda:tmp_path)
    monkeypatch.setattr(health,'get_cache',lambda:tmp_cache)
    monkeypatch.setattr(health,'_sync_watch_task',None)
    health._reset_import_state_for_tests()
    async def run():
        lock.acquire()
        entered.clear()
        stop=None
        try:
            assert (await health.trigger_hozz_sync())['status']=='started'
            assert await asyncio.to_thread(entered.wait,1)
            stop=asyncio.create_task(health.stop_health_background_tasks())
            await asyncio.wait_for(asyncio.shield(stop),.5)
            assert base.locked(),'Waiter must not release another sync owner'
            assert health._import_task is None
            assert (await health.import_status())['status']=='cancelled'
        finally:
            lock.release()
            if stop is not None: await stop
            await health.stop_health_background_tasks()
    asyncio.run(run())
