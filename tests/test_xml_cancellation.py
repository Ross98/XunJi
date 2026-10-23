"""XML cancellation must not publish success, and interrupted history must recover."""
import threading
import builtins
import pytest
import apple_health as ah
import cache as cache_module

@pytest.fixture(autouse=True)
def isolated_storage(isolated_health_db):
    yield

def source(tmp_path, monkeypatch, text='<HealthData/>'):
    path = tmp_path / 'synthetic.xml'
    path.write_text(text)
    monkeypatch.setattr(ah, 'EXPORT_XML', path)
    return path

def record(day, kind='record'):
    if kind == 'workout':
        return f'<Workout workoutActivityType="Walking" startDate="{day} 08:00:00 +0800" endDate="{day} 08:01:00 +0800" duration="1" durationUnit="min"/>'
    if kind == 'sleep':
        return f'<Record type="HKCategoryTypeIdentifierSleepAnalysis" startDate="{day} 08:00:00 +0800" endDate="{day} 08:01:00 +0800" value="AsleepCore"/>'
    return f'<Record type="steps" startDate="{day} 08:00:00 +0800" endDate="{day} 08:01:00 +0800" value="1" unit="count"/>'

def test_pre_cancelled_xml_does_no_initialization_or_marker_write(tmp_path, monkeypatch, tmp_cache):
    source(tmp_path, monkeypatch)
    event = threading.Event()
    event.set()
    calls = []

    def forbidden():
        calls.append(True)
        raise AssertionError('Cancelled XML importer initialized storage')
    monkeypatch.setattr(ah, '_init_db', forbidden)
    with pytest.raises(ah.ImportCancelled):
        ah.import_all(cache=tmp_cache, cancel_event=event)
    assert calls == []
    assert tmp_cache.get('_health_import_time') is None

@pytest.mark.parametrize('phase', ['initialize', 'watermarks', 'final_progress', 'end_of_file'])
def test_cancellation_at_short_import_boundaries_never_claims_success(tmp_path, monkeypatch, tmp_cache, phase):
    source(tmp_path, monkeypatch)
    event = threading.Event()
    marker = {'time': '2026-10-20T00:00:00Z'}
    tmp_cache.set('_health_import_time', marker)
    callback = None
    if phase in ('initialize', 'watermarks'):
        name = '_init_db' if phase == 'initialize' else '_load_import_watermarks'
        original = getattr(ah, name)

        def cancel_after(*args, **kwargs):
            result = original(*args, **kwargs)
            event.set()
            return result
        monkeypatch.setattr(ah, name, cancel_after)
    elif phase == 'final_progress':
        callback = lambda count: event.set()
    else:
        original = ah.ET.iterparse

        def cancel_eof(*args, **kwargs):
            yield from original(*args, **kwargs)
            event.set()
        monkeypatch.setattr(ah.ET, 'iterparse', cancel_eof)
    with pytest.raises(ah.ImportCancelled):
        ah.import_all(cache=tmp_cache, cancel_event=event, progress_callback=callback)
    assert tmp_cache.get('_health_import_time') == marker

def test_cancel_during_cache_resolution_is_not_swallowed_as_success(tmp_path, monkeypatch, tmp_cache):
    source(tmp_path, monkeypatch)
    event = threading.Event()

    def cache_factory():
        event.set()
        return tmp_cache
    monkeypatch.setattr(cache_module, 'Cache', cache_factory)
    with pytest.raises(ah.ImportCancelled):
        ah.import_all(cancel_event=event)
    assert tmp_cache.get('_health_import_time') is None

def test_cancelled_xml_closes_stream_and_retains_parsed_rows(tmp_path, monkeypatch, tmp_cache):
    path = source(tmp_path, monkeypatch, '<HealthData>' + record('2026-10-23') + record('2026-10-22') + '</HealthData>')
    event = threading.Event()
    streams = []

    def tracked_open(*args, **kwargs):
        stream = builtins.open(*args, **kwargs)
        streams.append(stream)
        return stream
    monkeypatch.setattr(ah, 'open', tracked_open, raising=False)
    original = ah._parse_record

    def cancel_first(elem):
        result = original(elem)
        event.set()
        return result
    monkeypatch.setattr(ah, '_parse_record', cancel_first)
    with pytest.raises(ah.ImportCancelled):
        ah.import_all(cache=tmp_cache, cancel_event=event)
    assert all((stream.closed for stream in streams))
    assert len(ah.query_records()) == 1
    assert tmp_cache.get('_health_import_time') is None

@pytest.mark.parametrize('kind', ['record', 'workout', 'sleep'])
def test_cancelled_descending_history_cannot_hide_unseen_older_records_on_retry(tmp_path, monkeypatch, tmp_cache, kind):
    source(tmp_path, monkeypatch, '<HealthData>' + record('2026-10-23', kind) + record('2010-01-01', kind) + '</HealthData>')
    query = {'record': ah.query_records, 'workout': ah.query_workouts, 'sleep': ah.query_sleep}[kind]
    event = threading.Event()
    with pytest.raises(ah.ImportCancelled):
        ah.import_all(cache=tmp_cache, batch_size=1, cancel_event=event, progress_callback=lambda count: event.set())
    assert len(query()) == 1
    with ah._conn() as conn:
        assert conn.execute('SELECT incomplete FROM xml_import_state').fetchone()[0] == 1
    event.clear()
    assert ah.import_all(cache=tmp_cache, cancel_event=event) == 1
    assert {row['start_date'][:10] for row in query()} == {'2026-10-23', '2010-01-01'}
    with ah._conn() as conn:
        assert conn.execute('SELECT incomplete FROM xml_import_state').fetchone()[0] == 0
    assert ah.import_all(cache=tmp_cache) == 0

def test_failed_xml_also_recovers_older_history_without_false_completion(tmp_path, monkeypatch, tmp_cache):
    path = source(tmp_path, monkeypatch, '<HealthData>' + record('2026-10-23') + '<broken>')
    with pytest.raises(ah.ET.ParseError):
        ah.import_all(cache=tmp_cache, batch_size=1)
    assert tmp_cache.get('_health_import_time') is None
    path.write_text('<HealthData>' + record('2026-10-23') + record('2010-01-01') + '</HealthData>')
    assert ah.import_all(cache=tmp_cache) == 1
    assert len(ah.query_records()) == 2

def test_wrong_xml_document_cannot_update_import_time(tmp_path, monkeypatch, tmp_cache):
    source(tmp_path, monkeypatch, '<UnrelatedDocument/>')
    with pytest.raises(ValueError):
        ah.import_all(cache=tmp_cache)
    assert tmp_cache.get('_health_import_time') is None

def test_unconfirmed_legacy_rows_do_not_skip_unseen_history(tmp_path, monkeypatch, tmp_cache):
    path = source(tmp_path, monkeypatch, '<HealthData>' + record('2026-10-23') + '</HealthData>')
    assert ah.import_all(cache=tmp_cache) == 1
    with ah._conn() as conn:
        conn.execute('DELETE FROM xml_import_state')
    path.write_text('<HealthData>' + record('2010-01-01') + '</HealthData>')
    assert ah.import_all(cache=tmp_cache) == 1
    assert len(ah.query_records()) == 2

def test_oldest_valid_watermark_does_not_underflow():
    assert not ah._is_before_import_window('0001-01-01 00:00:00 +0000', '0001-01-01 00:00:00 +0000')

def test_import_cancellation_from_cache_factory_propagates(tmp_path, monkeypatch):
    source(tmp_path, monkeypatch)

    def cancelled_cache():
        raise ah.ImportCancelled('cancelled while resolving cache')
    monkeypatch.setattr(cache_module, 'Cache', cancelled_cache)
    with pytest.raises(ah.ImportCancelled):
        ah.import_all()
    with ah._conn() as conn:
        assert conn.execute('SELECT incomplete FROM xml_import_state').fetchone()[0] == 1

def test_process_interruption_checkpoint_survives_restart(tmp_path, monkeypatch, tmp_cache):
    import subprocess
    import sys
    path = source(tmp_path, monkeypatch, '<HealthData>' + record('2026-10-23') + record('2010-01-01') + '</HealthData>')
    script = '\nimport os, sys\nfrom pathlib import Path\nimport apple_health as ah\nfrom cache import Cache\nah.HEALTH_DB_PATH = Path(sys.argv[1])\nah.EXPORT_XML = Path(sys.argv[2])\nah.import_all(cache=Cache(sys.argv[3]), batch_size=1,\n              progress_callback=lambda count: os._exit(73))\n'
    result = subprocess.run([sys.executable, '-c', script, str(ah.HEALTH_DB_PATH), str(path), str(tmp_path / 'child-cache.sqlite')], capture_output=True, timeout=10)
    assert result.returncode == 73, result.stderr.decode()
    with ah._conn() as conn:
        assert conn.execute('SELECT incomplete FROM xml_import_state').fetchone()[0] == 1
    assert len(ah.query_records()) == 1
    assert ah.import_all(cache=tmp_cache) == 1
    assert len(ah.query_records()) == 2
