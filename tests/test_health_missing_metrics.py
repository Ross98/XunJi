"""Legacy missing/bad measurements keep their dates without breaking the page."""

import asyncio
import json
import re
from datetime import date, timedelta

import pytest
from starlette.requests import Request

from routes import health

METRICS = [
    'HeartRateVariabilitySDNN', 'RestingHeartRate', 'VO2Max',
    'OxygenSaturation', 'RespiratoryRate', 'HeartRateRecoveryOneMinute',
    'AppleSleepingWristTemperature', 'WalkingSpeed', 'WalkingStepLength',
    'WalkingAsymmetryPercentage', 'AppleWalkingSteadiness', 'BodyMass', 'BodyFatPercentage',
]


@pytest.mark.parametrize('value', [None, float('inf'), float('-inf'), 'bad'])
def test_invalid_latest_metrics_render_missing_and_keep_chart_gaps(
    isolated_health_db, tmp_cache, monkeypatch, tmp_path, value
):
    ah = isolated_health_db
    today = date.today()
    older = (today - timedelta(days=1)).isoformat()
    records = []
    for metric in METRICS:
        for day, measurement in [(older, 12), (today.isoformat(), value)]:
            records.append(dict(type='HKQuantityTypeIdentifier' + metric,
                start_date=day + ' 08:00:00 +0800', end_date=day + ' 08:01:00 +0800',
                value=measurement, unit='test', source_name='synthetic'))
    ah._bulk_insert(records, [])
    monkeypatch.setattr(health, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(health.hs, 'source_folder', lambda: tmp_path)
    monkeypatch.setattr(ah, 'EXPORT_XML', tmp_path / 'missing.xml')

    response = asyncio.run(health.health_page(Request({'type': 'http', 'path': '/health', 'headers': []})))
    html = response.body.decode()
    assert response.status_code == 200
    cards = re.findall(r'class="card health-metric-card">(.*?)</div>', html)
    assert len(cards) == 7
    assert all('<strong>--</strong>' in card for card in cards)
    assert all(today.isoformat() in card for card in cards)
    for chart in ['healthHrvChart', 'healthRhrChart', 'healthVo2Chart']:
        payload = re.search(r"drawHealthChart\('" + chart + r"', (.*), '#", html).group(1)
        points = json.loads(payload, parse_constant=lambda value: pytest.fail('nonfinite JSON: ' + value))
        assert [point['value'] for point in points] == [None, 12]
        assert points[0]['start_date'].startswith(today.isoformat())
    assert ah.get_import_count()['total_records'] == len(METRICS) * 2
    raw = ah.query_records_desc('HKQuantityTypeIdentifierHeartRateVariabilitySDNN')[0]
    assert raw['value'] == value


def test_zero_health_metric_remains_a_real_measurement(isolated_health_db, tmp_cache, monkeypatch, tmp_path):
    ah = isolated_health_db
    day = date.today().isoformat()
    ah._bulk_insert([dict(type='HKQuantityTypeIdentifierHeartRateRecoveryOneMinute',
        start_date=day + ' 08:00:00 +0800', end_date=day + ' 08:01:00 +0800',
        value=0, unit='count/min', source_name='synthetic')], [])
    monkeypatch.setattr(health, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(health.hs, 'source_folder', lambda: tmp_path)
    response = asyncio.run(health.health_page(Request({'type': 'http', 'path': '/health', 'headers': []})))
    assert '<span>一分钟心率恢复</span><strong>0</strong>' in response.body.decode()


def test_percentage_conversion_overflow_remains_missing():
    assert health._format_health_value([{'value': 1e308}], 1, 100) == '--'
    assert health._build_body_table([], [
        {'start_date': '2026-10-23 08:00:00 +0800', 'value': 1e308}
    ]) == [{'date': '2026-10-23'}]
