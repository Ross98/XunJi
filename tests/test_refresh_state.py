import asyncio

import httpx
import pytest

import app_state
from app import app
from data_service import DataService


def request(path, method='GET'):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://test'
        ) as client:
            response = await client.request(method, path)
        assert response.status_code == 200
        return response.json()
    return asyncio.run(run())


def test_clear_only_removes_data_and_never_fakes_successful_refresh(tmp_cache, monkeypatch):
    monkeypatch.setattr(app_state, 'get_cache', lambda: tmp_cache)
    markers = {
        '_health_import_time': {'time': '2026-10-20T08:00:00+00:00'},
        '_xunji_refresh_time': {'time': '2026-10-19T08:00:00+00:00'},
        '_other_state': {'keep': True},
    }
    for key, value in markers.items():
        tmp_cache.set(key, value)
    tmp_cache.set('training:day', {'trains': []})
    tmp_cache.set('body:day', {'records': []})
    assert request('/admin/clear-cache', 'POST')['status'] == 'ok'
    for key, value in markers.items():
        assert tmp_cache.get(key) == value
    assert tmp_cache.get('training:day') is None
    assert tmp_cache.get('body:day') is None


def test_cold_clear_does_not_claim_data_has_been_fetched(tmp_cache, monkeypatch):
    monkeypatch.setattr(app_state, 'get_cache', lambda: tmp_cache)
    assert request('/admin/clear-cache', 'POST')['status'] == 'ok'
    assert request('/admin/refresh-times') == {'xunji': None, 'health': None}


@pytest.mark.parametrize('stored,expected', [
    ('2026-10-22T23:00:00Z', '2026-10-23T07:00:00'),
    ('2026-10-22T23:00:00+02:00', '2026-10-23T05:00:00'),
    ('2026-10-22T08:00:00+08:00', '2026-10-22T08:00:00'),
    ('2026-10-22T08:00:00', '2026-10-22T08:00:00'),
    ('brokenZ', None), ([], None),
])
def test_refresh_times_handle_offsets_and_malformed_markers(tmp_cache, monkeypatch, stored, expected):
    monkeypatch.setattr(app_state, 'get_cache', lambda: tmp_cache)
    tmp_cache.set('_xunji_refresh_time', {'time': stored})
    assert request('/admin/refresh-times')['xunji'] == expected


def test_refresh_marker_only_advances_after_successful_upstream_response(tmp_cache, monkeypatch):
    monkeypatch.setattr(app_state, 'get_cache', lambda: tmp_cache)
    previous = {'time': '2026-10-19T08:00:00+00:00'}
    tmp_cache.set('_xunji_refresh_time', previous)
    request('/admin/clear-cache', 'POST')

    class Client:
        response = {'success': False, 'res': 'unavailable'}

        async def fetch_training(self, *args):
            return self.response

    client = Client()
    service = DataService(tmp_cache, client)
    asyncio.run(service.get_training('2026-10-22'))
    assert tmp_cache.get('_xunji_refresh_time') == previous
    client.response = {'res': {'trains': []}}
    asyncio.run(service.get_training('2026-10-22'))
    assert tmp_cache.get('_xunji_refresh_time') != previous
