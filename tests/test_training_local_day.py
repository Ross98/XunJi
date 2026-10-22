import asyncio
import os
import time
from datetime import datetime, timezone

import pytest

from routes import training


@pytest.fixture
def local_zone():
    previous = os.environ.get('TZ')

    def select(zone):
        os.environ['TZ'] = zone
        time.tzset()

    yield select
    if previous is None:
        os.environ.pop('TZ', None)
    else:
        os.environ['TZ'] = previous
    time.tzset()


@pytest.mark.parametrize('zone,instant,expected', [
    ('Asia/Shanghai', '2026-10-22T17:00:00+00:00', '2026-10-23'),
    ('America/Los_Angeles', '2026-10-23T01:00:00+00:00', '2026-10-22'),
])
def test_backfill_starts_on_local_calendar_day(local_zone, zone, instant, expected):
    local_zone(zone)

    class Cache:
        def get(self, key):
            return None

    class Service:
        calls = []

        async def get_training(self, day, **kwargs):
            self.calls.append(day)

    service = Service()
    asyncio.run(training._backfill_training_history(
        service, Cache(), days=1, delay_seconds=0, now=datetime.fromisoformat(instant)))
    assert service.calls == [expected]


def test_backfill_default_clock_uses_local_today(local_zone, monkeypatch):
    local_zone('Asia/Shanghai')

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = cls(2026, 10, 22, 17, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz else instant.astimezone().replace(tzinfo=None)

    monkeypatch.setattr(training, 'datetime', Clock)
    called = []

    class Cache:
        def get(self, key):
            return None

    class Service:
        async def get_training(self, day, **kwargs):
            called.append(day)

    asyncio.run(training._backfill_training_history(Service(), Cache(), days=1, delay_seconds=0))
    assert called == ['2026-10-23']
