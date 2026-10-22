import asyncio
from copy import deepcopy

import pytest

import data_service
from data_service import DataService, extract_body_records

START, END = '2026-01-01', '2026-10-23'
KEY = f'body:all:{START}:{END}:2'
ROWS = [{'datestr': f'2026-10-{day:02d}', 'type': 'weight', 'value': 70}
        for day in [23, 22, 21, 20, 19]]


class Client:
    def __init__(self, pages):
        self.pages = pages
        self.offsets = []

    async def query_body(self, start, end, types, limit, offset=0):
        self.offsets.append(offset)
        page = self.pages[len(self.offsets) - 1]
        if isinstance(page, Exception):
            raise page
        return page


@pytest.fixture
def delays(monkeypatch):
    values = []

    async def sleep(seconds):
        values.append(seconds)

    monkeypatch.setattr(data_service, '_body_page_sleep', sleep)
    return values


def test_all_body_pages_are_merged_before_caching(tmp_cache, delays):
    pages = [{'success': True, 'res': {'records': ROWS[:2], 'latest': {'weight': ROWS[0]}}},
             {'records': ROWS[2:4]}, {'records': ROWS[4:]}]
    original = deepcopy(pages)
    client = Client(pages)
    service = DataService(tmp_cache, client)
    result = asyncio.run(service.get_body(START, END, limit=2))
    assert extract_body_records(result) == ROWS
    assert result['res']['latest'] == {'weight': ROWS[0]}
    assert client.offsets == [0, 2, 4]
    assert delays == [15, 15]
    assert pages == original
    assert tmp_cache.get(KEY) == result
    assert asyncio.run(service.get_body(START, END, limit=2)) == result
    assert client.offsets == [0, 2, 4]


def test_exact_page_boundary_requires_empty_terminal_page(tmp_cache, delays):
    client = Client([{'records': ROWS[:2]}, {'records': []}])
    result = asyncio.run(DataService(tmp_cache, client).get_body(START, END, limit=2))
    assert extract_body_records(result) == ROWS[:2]
    assert client.offsets == [0, 2]


@pytest.mark.parametrize('failure', [RuntimeError('offline'), {'success': False, 'res': 'too frequent'}])
def test_failed_later_page_never_replaces_complete_cache(tmp_cache, delays, failure):
    old = {'records': ROWS[:1]}
    tmp_cache.set(KEY, old)
    client = Client([{'records': ROWS[:2]}, failure])
    result = asyncio.run(DataService(tmp_cache, client).get_body(START, END, limit=2, force_refresh=True))
    assert result == old
    assert tmp_cache.get(KEY) == old
    assert tmp_cache.get('_xunji_refresh_time') is None
    assert client.offsets == [0, 2]


def test_legacy_full_page_cache_is_upgraded(tmp_cache, delays):
    tmp_cache.set(KEY, {'records': ROWS[:2]})
    client = Client([{'records': ROWS[:2]}, {'records': ROWS[2:3]}])
    result = asyncio.run(DataService(tmp_cache, client).get_body(START, END, limit=2))
    assert extract_body_records(result) == ROWS[:3]
    assert client.offsets == [0, 2]


def test_repeating_page_fails_without_caching_partial_history(tmp_cache, delays):
    client = Client([{'records': ROWS[:2]}, {'records': ROWS[:2]}])
    with pytest.raises(ValueError, match='pagination'):
        asyncio.run(DataService(tmp_cache, client).get_body(START, END, limit=2))
    assert tmp_cache.get(KEY) is None
    assert client.offsets == [0, 2]


def test_default_500_record_pages_cover_large_history(tmp_cache, delays):
    from datetime import date, timedelta
    from analysis import summarize_body
    from routes.body import _build_table_data
    rows = [{'datestr': (date(2026, 10, 23) - timedelta(days=i)).isoformat(),
             'type': 'weight', 'value': 70 + i % 20 / 10} for i in range(1001)]
    client = Client([{'records': rows[:500]}, {'records': rows[500:1000]}, {'records': rows[1000:]}])
    result = asyncio.run(DataService(tmp_cache, client).get_body('2020-01-01', END))
    assert client.offsets == [0, 500, 1000]
    records = extract_body_records(result)
    assert records == rows
    assert len(summarize_body(records)['weight_trend']) == 1001
    assert len(_build_table_data(records, {})) == 1001


@pytest.mark.parametrize('limit', [0, -1, True, 1.5])
def test_invalid_page_size_fails_before_network(tmp_cache, delays, limit):
    client = Client([])
    with pytest.raises(ValueError, match='positive integer'):
        asyncio.run(DataService(tmp_cache, client).get_body(START, END, limit=limit))
    assert client.offsets == []


def test_pagination_wait_can_be_cancelled_without_losing_cache(tmp_cache, monkeypatch):
    async def cancel(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(data_service, '_body_page_sleep', cancel)
    old = {'records': ROWS[:1]}
    tmp_cache.set(KEY, old)
    client = Client([{'records': ROWS[:2]}])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(DataService(tmp_cache, client).get_body(START, END, limit=2, force_refresh=True))
    assert tmp_cache.get(KEY) == old
    assert client.offsets == [0]


def test_dashboard_does_not_treat_legacy_full_page_as_complete():
    from routes.dashboard import _get_body_records
    from fastapi import BackgroundTasks
    legacy = {'records': [ROWS[0]] * 500}
    fresh = {'records': ROWS}

    class Cache:
        def get(self, key):
            return legacy

    class Service:
        calls = 0

        async def get_body(self, start, end):
            self.calls += 1
            return fresh

    service = Service()
    tasks = BackgroundTasks()
    result = asyncio.run(_get_body_records(service, Cache(), 'body:key', START, END, tasks))
    assert result == ROWS
    assert service.calls == 1
    assert tasks.tasks == []


def test_string_encoded_pages_and_type_groups_are_reconciled(tmp_cache, delays):
    import json
    client = Client([{'res': {'records': json.dumps(ROWS[:2]), 'by_type': {'weight': ROWS[:2]}}},
                     {'res': {'records': json.dumps(ROWS[2:3])}}])
    result = asyncio.run(DataService(tmp_cache, client).get_body(START, END, limit=2))
    assert result['res']['records'] == ROWS[:3]
    assert result['res']['by_type'] == {'weight': ROWS[:3]}
