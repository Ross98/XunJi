"""XML availability is rechecked without starting a doomed import job."""
import asyncio
import threading

import pytest

from routes import health


@pytest.fixture(autouse=True)
def reset_job_state():
    health._reset_import_state_for_tests()
    yield
    health._reset_import_state_for_tests()


@pytest.mark.parametrize('present', [False, True])
def test_import_status_reports_current_regular_file_availability(tmp_path, monkeypatch, present):
    source = tmp_path / 'export.xml'
    if present:
        source.write_text('<HealthData/>')
    monkeypatch.setattr(health.ah, 'EXPORT_XML', source)
    result = asyncio.run(health.import_status())
    assert result['export_available'] is present
    assert result['kind'] is None
    assert result['status'] == 'idle'


def test_directory_is_not_an_available_xml_export(tmp_path, monkeypatch):
    monkeypatch.setattr(health.ah, 'EXPORT_XML', tmp_path)
    assert asyncio.run(health.import_status())['export_available'] is False


def test_missing_xml_does_not_start_task_or_replace_previous_result(tmp_path, monkeypatch):
    monkeypatch.setattr(health.ah, 'EXPORT_XML', tmp_path / 'missing.xml')
    health._set_import_state(status='ok', kind='hozz', total=3, message='Hozz 同步完成')
    result = asyncio.run(health.trigger_import())
    assert result['status'] == 'error'
    assert result['export_available'] is False
    assert health._import_task is None
    status = asyncio.run(health.import_status())
    assert status['kind'] == 'hozz'
    assert status['total'] == 3


def test_file_added_after_page_load_can_start_import(tmp_path, monkeypatch):
    source = tmp_path / 'export.xml'
    monkeypatch.setattr(health.ah, 'EXPORT_XML', source)
    monkeypatch.setattr(health, 'get_cache', lambda: object())
    monkeypatch.setattr(health.ah, 'import_all', lambda **kwargs: 0)

    async def run():
        assert (await health.import_status())['export_available'] is False
        source.write_text('<HealthData/>')
        assert (await health.trigger_import())['status'] == 'started'
        await health._import_task
        status = await health.import_status()
        assert status['kind'] == 'xml'
        assert status['status'] == 'ok'
        source.unlink()
        assert (await health.import_status())['export_available'] is False

    asyncio.run(run())


def test_availability_lookup_does_not_block_running_job_lock(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    owner = threading.get_ident()

    def slow_info():
        assert threading.get_ident() != owner
        entered.set()
        assert release.wait(2)
        return {'available': True, 'size_mb': 0}

    monkeypatch.setattr(health, '_xml_export_info', slow_info)

    async def run():
        request = asyncio.create_task(health.trigger_import())
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            health._set_import_state(status='running', kind='hozz')
            duplicate = await asyncio.wait_for(health.trigger_import(), .5)
            assert duplicate['status'] == 'busy'
        finally:
            release.set()
            result = await request
        assert result['status'] == 'busy'
        assert health._import_task is None

    asyncio.run(run())
