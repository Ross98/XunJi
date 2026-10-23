"""Freshness must not turn malformed or future source dates into fresh health data."""

from datetime import date, timedelta

import pytest

from data_service import DataFreshnessService


class HealthDates:
    def __init__(self, latest):
        self.latest = latest

    def get_latest_dates(self):
        return {'HKQuantityTypeIdentifierHeartRateVariabilitySDNN': self.latest}

    def get_data_coverage(self):
        return {'start_date': None, 'end_date': self.latest}


@pytest.mark.parametrize('latest', ['not-a-date', '2026-02-30', '20261023', '', 123, [], {}])
def test_malformed_health_date_is_unknown_without_crashing(latest):
    result = DataFreshnessService(HealthDates(latest)).get_freshness_context()
    assert result['ah_status'] == 'unknown'
    assert result['ah_days_ago'] is None
    assert result['ah_tag'] == '[日期未确认]'
    hrv = result['metrics']['hrv']
    assert (hrv['status'], hrv['days_ago'], hrv['tag']) == ('unknown', None, '[日期未确认]')
    assert hrv['latest'] == latest
    assert result['ah_overall_latest'] == latest


@pytest.mark.parametrize('latest', [(date.today() + timedelta(days=1)).isoformat(), date.max.isoformat()])
def test_future_health_date_never_claims_freshness(latest):
    result = DataFreshnessService(HealthDates(latest)).get_freshness_context()
    assert (result['ah_status'], result['ah_days_ago'], result['ah_tag']) == (
        'future', None, '[未来日期，待核对]')
    hrv = result['metrics']['hrv']
    assert (hrv['status'], hrv['days_ago'], hrv['tag']) == ('future', None, '[未来日期，待核对]')
    assert result['ah_overall_latest'] == latest


@pytest.mark.parametrize('days,overall,metric', [
    (0, 'fresh', 'today'), (1, 'fresh', 'recent'), (3, 'fresh', 'recent'),
    (4, 'stale', 'stale'), (7, 'stale', 'stale'), (8, 'expired', 'expired'),
])
def test_valid_freshness_boundaries_remain_unchanged(days, overall, metric):
    latest = (date.today() - timedelta(days=days)).isoformat()
    result = DataFreshnessService(HealthDates(latest)).get_freshness_context()
    assert (result['ah_status'], result['ah_days_ago']) == (overall, days)
    assert (result['metrics']['hrv']['status'], result['metrics']['hrv']['days_ago']) == (metric, days)


def test_absent_health_date_still_means_not_imported():
    result = DataFreshnessService(HealthDates(None)).get_freshness_context()
    assert (result['ah_status'], result['ah_tag']) == ('none', '[未导入]')


@pytest.mark.parametrize('latest,tag', [
    ('not-a-date', '[日期未确认]'),
    ((date.today() + timedelta(days=1)).isoformat(), '[未来日期，待核对]'),
])
def test_health_page_survives_anomalous_sqlite_dates(
    isolated_health_db, tmp_cache, monkeypatch, tmp_path, latest, tag
):
    import asyncio
    import httpx
    from fastapi import FastAPI
    from routes import health

    ah = isolated_health_db
    ah._bulk_insert([dict(type='HKQuantityTypeIdentifierHeartRateVariabilitySDNN',
        start_date=latest + ' 08:00:00 +0800', end_date=latest + ' 08:01:00 +0800',
        value=42, unit='ms', source_name='synthetic')], [])
    original = ah.query_records()
    monkeypatch.setattr(health, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(health.hs, 'source_folder', lambda: tmp_path)
    monkeypatch.setattr(ah, 'EXPORT_XML', tmp_path / 'missing.xml')
    app = FastAPI()
    app.include_router(health.router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
            response = await client.get('/health')
            assert response.status_code == 200
            assert tag in response.text
            assert 'class="freshness-bar freshness-unknown"' in response.text
            assert 'class="freshness-bar freshness-fresh"' not in response.text
            assert latest in response.text

    asyncio.run(run())
    assert ah.query_records() == original
