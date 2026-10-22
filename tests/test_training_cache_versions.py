import asyncio
from datetime import datetime, timezone

import pytest

from data_service import DataService, get_cached_training
from routes import training

DAY = '2026-08-03'


def populated(marker):
    return {'trains': [{'datestr': DAY, 'title': marker, 'movements': []}]}


def seed(cache, mode, data, timestamp):
    key = f'training:{DAY}:{mode}'
    cache.set(key, data)
    with cache._conn() as conn:
        conn.execute('UPDATE cache SET mtime=? WHERE key=?', (timestamp, key))


@pytest.mark.parametrize('full,summary,expected', [
    (populated('old'), {'trains': []}, {'trains': []}),
    ({'trains': []}, populated('new'), populated('new')),
    (populated('old'), populated('new'), populated('new')),
])
def test_both_read_modes_choose_the_latest_valid_snapshot(tmp_cache, full, summary, expected):
    seed(tmp_cache, True, full, '2026-08-03T08:00:00+00:00')
    seed(tmp_cache, False, summary, '2026-08-03T09:00:00+00:00')
    assert get_cached_training(tmp_cache, DAY, full=True) == expected
    assert get_cached_training(tmp_cache, DAY, full=False) == expected


def test_equal_versions_prefer_full_data_for_both_readers(tmp_cache):
    seed(tmp_cache, True, populated('full'), '2026-08-03T09:00:00+00:00')
    seed(tmp_cache, False, populated('summary'), '2026-08-03T09:00:00+00:00')
    assert get_cached_training(tmp_cache, DAY) == populated('full')


def test_mixed_aware_and_legacy_naive_timestamps_can_be_compared(tmp_cache):
    seed(tmp_cache, True, populated('old'), '2026-08-03T08:00:00')
    seed(tmp_cache, False, populated('new'), '2026-08-03T09:00:00Z')
    assert get_cached_training(tmp_cache, DAY, True) == populated('new')


def test_calendar_does_not_resurrect_a_newer_empty_day(tmp_cache, monkeypatch):
    seed(tmp_cache, True, populated('old'), '2026-08-03T08:00:00+00:00')
    seed(tmp_cache, False, {'trains': []}, '2026-08-03T09:00:00+00:00')
    monkeypatch.setattr(training, 'get_cache', lambda: tmp_cache)
    assert DAY not in training._get_cached_training_dates()


def test_full_read_upgrades_a_newer_summary_snapshot(tmp_cache):
    seed(tmp_cache, True, populated('old'), '2026-08-03T08:00:00+00:00')
    seed(tmp_cache, False, populated('new summary'), '2026-08-03T09:00:00+00:00')

    class Client:
        calls = 0

        async def fetch_training(self, day, full):
            assert full is True
            self.calls += 1
            return populated('fresh full')

    client = Client()
    assert asyncio.run(DataService(tmp_cache, client).get_training(DAY, True)) == populated('fresh full')
    assert client.calls == 1
    assert get_cached_training(tmp_cache, DAY) == populated('fresh full')


def test_failed_backfill_does_not_delete_existing_snapshots(tmp_cache):
    old = {'trains': []}
    seed(tmp_cache, True, old, '2026-08-03T00:00:00+00:00')
    seed(tmp_cache, False, old, '2026-08-03T00:00:00+00:00')

    class Service:
        async def get_training(self, *args, **kwargs):
            raise RuntimeError('upstream unavailable')

    asyncio.run(training._backfill_training_history(
        Service(), tmp_cache, days=1, today=datetime(2026, 8, 3).date(),
        now=datetime(2026, 8, 3, 2, tzinfo=timezone.utc), delay_seconds=0))
    assert tmp_cache.get(f'training:{DAY}:True') == old
    assert tmp_cache.get(f'training:{DAY}:False') == old


def test_summary_only_day_is_eligible_for_full_history_backfill(tmp_cache):
    seed(tmp_cache, False, populated('summary'), '2026-08-03T00:00:00+00:00')
    assert not training._should_skip_training_backfill(
        tmp_cache, DAY, datetime(2026, 8, 3, 2, tzinfo=timezone.utc))


@pytest.mark.parametrize('failure', ['exception', 'invalid'])
def test_failed_upgrade_preserves_snapshot_and_throttles_retry(tmp_cache, monkeypatch, failure):
    import data_service

    snapshot = populated('summary')
    seed(tmp_cache, False, snapshot, '2026-08-03T09:00:00+00:00')
    clock = [100.0]
    monkeypatch.setattr(data_service, 'monotonic', lambda: clock[0])

    class Client:
        calls = 0

        async def fetch_training(self, day, full):
            self.calls += 1
            if failure == 'exception':
                raise RuntimeError('upstream unavailable')
            return {'error': 'rate limited'}

    client = Client()
    service = DataService(tmp_cache, client)
    assert asyncio.run(service.get_training(DAY, True)) == snapshot
    clock[0] += 29
    assert asyncio.run(service.get_training(DAY, True)) == snapshot
    assert client.calls == 1
    clock[0] += 1
    assert asyncio.run(service.get_training(DAY, True)) == snapshot
    assert client.calls == 2
    assert get_cached_training(tmp_cache, DAY) == snapshot
    assert tmp_cache.get(f'training:{DAY}:True') is None
