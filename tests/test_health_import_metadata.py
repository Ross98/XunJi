"""Import freshness needs one configured marker read, never a health-table scan."""
import asyncio
import sqlite3
import threading
from contextlib import contextmanager

import httpx
import pytest
from fastapi import FastAPI

import cache as cache_module
from cache import Cache
from routes import dashboard, health

STAMP = '2026-10-23T08:00:00+00:00'


@pytest.fixture(autouse=True)
def isolate_storage(isolated_health_db):
    """Even failing pre-fix requests must stay outside real health storage."""
    yield


def request_check():
    app = FastAPI()
    app.include_router(dashboard.router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
            response = await client.get('/api/ah-check')
            assert response.status_code == 200
            return response.json()
    return asyncio.run(run())


def test_freshness_endpoint_reads_configured_cache_without_touching_health_store(
    isolated_health_db, tmp_cache, monkeypatch
):
    tmp_cache.set('_health_import_time', {'time': STAMP})
    monkeypatch.setattr(dashboard, 'get_cache', lambda: tmp_cache)

    def no_health_scan(*args, **kwargs):
        raise AssertionError('Freshness endpoint must not query the health database')

    monkeypatch.setattr(isolated_health_db, '_conn', no_health_scan)
    assert request_check() == {'import_time': STAMP, 'ok': True}


def test_freshness_read_runs_outside_event_loop_and_only_reads_one_key(monkeypatch):
    owner = threading.get_ident()
    reads = []

    class MarkerCache:
        def get(self, key):
            assert threading.get_ident() != owner, 'Cache read blocked event loop'
            reads.append(key)
            return {'time': STAMP}

    monkeypatch.setattr(dashboard, 'get_cache', MarkerCache)
    assert request_check() == {'import_time': STAMP, 'ok': True}
    assert reads == ['_health_import_time']


@pytest.mark.parametrize('marker', [None, {}, {'other': 'value'}, {'time': None}, {'time': []},
                                    {'time': True}, {'time': 123}, {'time': 'broken'}])
def test_malformed_marker_is_unknown_without_counting_health_rows(monkeypatch, marker):
    class MarkerCache:
        def get(self, key): return marker
    monkeypatch.setattr(dashboard, 'get_cache', MarkerCache)
    assert request_check() == {'import_time': None, 'ok': True}


def test_counts_without_cache_do_not_construct_or_write_a_default_cache(
    isolated_health_db, tmp_path, monkeypatch
):
    calls = []
    def forbidden_cache(*args, **kwargs):
        calls.append(True)
        raise AssertionError('Statistics must not bootstrap a cache')

    monkeypatch.setattr(cache_module, 'Cache', forbidden_cache)
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    counts = isolated_health_db.get_import_count()
    assert counts['total_records'] == counts['total_workouts'] == counts['total_sleep'] == 0
    assert counts['health_import_time'] is None
    assert calls == []
    assert not (work / 'xunji_cache.sqlite').exists()


def test_counts_use_injected_marker_and_accept_read_only_connections(isolated_health_db, tmp_cache, monkeypatch):
    tmp_cache.set('_health_import_time', {'time': STAMP})
    uri = isolated_health_db.HEALTH_DB_PATH.as_uri() + '?mode=ro'

    @contextmanager
    def read_only():
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try: yield conn
        finally: conn.close()

    monkeypatch.setattr(isolated_health_db, '_conn', read_only)
    assert isolated_health_db.get_import_count(cache=tmp_cache)['health_import_time'] == STAMP


def test_default_cache_honors_explicit_environment_path_without_api_keys(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, 'PROJECT_ENV_PATH', tmp_path / 'absent.env')
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    selected = tmp_path / 'selected.sqlite'
    monkeypatch.setenv('XUNJI_CACHE_PATH', str(selected))
    monkeypatch.delenv('XUNJI_API_KEY', raising=False)
    monkeypatch.delenv('XUNJI_BODY_API_KEY', raising=False)
    Cache().set('_health_import_time', {'time': STAMP})
    assert selected.exists()
    assert not (work / 'xunji_cache.sqlite').exists()
    assert Cache(str(selected)).get('_health_import_time') == {'time': STAMP}


def test_default_cache_loads_only_path_from_project_dotenv(tmp_path, monkeypatch):
    import config
    selected = tmp_path / 'dotenv-cache.sqlite'
    dotenv = tmp_path / 'settings.env'
    dotenv.write_text('XUNJI_CACHE_PATH=' + str(selected) + '\n')
    monkeypatch.setattr(config, 'PROJECT_ENV_PATH', dotenv)
    monkeypatch.delenv('XUNJI_CACHE_PATH', raising=False)
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    Cache().set('test', {})
    assert selected.exists()
    assert not (work / 'xunji_cache.sqlite').exists()


def test_health_overview_passes_runtime_cache_into_statistics(
    isolated_health_db, tmp_cache, tmp_path, monkeypatch
):
    from starlette.requests import Request
    original = isolated_health_db.get_import_count
    seen = []

    def counts(*, cache=None):
        seen.append(cache)
        return original(cache=cache)

    monkeypatch.setattr(isolated_health_db, 'get_import_count', counts)
    monkeypatch.setattr(health, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(health.hs, 'source_folder', lambda: tmp_path)
    monkeypatch.setattr(isolated_health_db, 'EXPORT_XML', tmp_path / 'absent.xml')
    response = asyncio.run(health.health_page(Request({'type':'http','path':'/health','headers':[]})))
    assert response.status_code == 200
    assert seen == [tmp_cache]


@pytest.mark.parametrize('blank', ['', '   '])
def test_blank_cache_setting_uses_persistent_default_for_app_and_standalone(tmp_path, monkeypatch, blank):
    import config
    monkeypatch.setattr(config, 'PROJECT_ENV_PATH', tmp_path / 'absent.env')
    monkeypatch.setenv('XUNJI_CACHE_PATH', blank)
    monkeypatch.setenv('XUNJI_API_KEY', 'synthetic-training')
    monkeypatch.setenv('XUNJI_BODY_API_KEY', 'synthetic-body')
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    assert config.XunjiConfig.from_env().cache_path == 'xunji_cache.sqlite'
    standalone = Cache()
    standalone.set('persistent', {'ok': True})
    assert Cache().get('persistent') == {'ok': True}
    assert (work / 'xunji_cache.sqlite').exists()


def test_explicit_cache_path_is_not_overridden_by_environment(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, 'PROJECT_ENV_PATH', tmp_path / 'absent.env')
    other = tmp_path / 'other.sqlite'
    monkeypatch.setenv('XUNJI_CACHE_PATH', str(other))
    selected = tmp_path / 'selected.sqlite'
    Cache(str(selected)).set('explicit', {'ok': True})
    assert selected.exists()
    assert not other.exists()


def test_unavailable_marker_is_reported_without_health_scan(monkeypatch):
    class FailingCache:
        def get(self, key): raise sqlite3.OperationalError('synthetic read failure')
    monkeypatch.setattr(dashboard, 'get_cache', FailingCache)
    result = request_check()
    assert result['ok'] is False
    assert result['import_time'] is None


def test_marker_failure_does_not_hide_other_health_statistics(isolated_health_db):
    class FailingCache:
        def get(self, key): raise sqlite3.OperationalError('synthetic read failure')
    counts = isolated_health_db.get_import_count(cache=FailingCache())
    assert counts['total_records'] == counts['total_workouts'] == counts['total_sleep'] == 0
    assert counts['health_import_time'] is None


@pytest.mark.parametrize('stamp', ['2026-10-23T08:00:00Z', '2026-10-23T08:00:00+08:00', '2026-10-23 08:00:00'])
def test_existing_timestamp_formats_are_preserved_without_rewrite(tmp_cache, monkeypatch, stamp):
    tmp_cache.set('_health_import_time', {'time': stamp})
    before = tmp_cache.get_mtime('_health_import_time')
    monkeypatch.setattr(dashboard, 'get_cache', lambda: tmp_cache)
    assert request_check() == {'ok': True, 'import_time': stamp}
    assert tmp_cache.get_mtime('_health_import_time') == before


def test_standalone_xml_import_writes_marker_to_configured_cache(
    isolated_health_db, tmp_path, monkeypatch
):
    import config
    source = tmp_path / 'synthetic.xml'
    source.write_text('''<HealthData><Record type="HKQuantityTypeIdentifierStepCount"
        startDate="2026-10-23 08:00:00 +0800" endDate="2026-10-23 08:01:00 +0800"
        value="42" unit="count" /></HealthData>''')
    selected = tmp_path / 'selected.sqlite'
    monkeypatch.setattr(isolated_health_db, 'EXPORT_XML', source)
    monkeypatch.setattr(config, 'PROJECT_ENV_PATH', tmp_path / 'absent.env')
    monkeypatch.setenv('XUNJI_CACHE_PATH', str(selected))
    monkeypatch.setattr(cache_module, 'Cache', Cache)
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    assert isolated_health_db.import_all() == 1
    assert isolated_health_db.get_import_time(Cache(str(selected))) is not None
    assert not (work / 'xunji_cache.sqlite').exists()
