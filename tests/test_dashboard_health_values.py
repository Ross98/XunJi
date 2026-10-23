"""A missing or corrupt legacy measurement must not break the dashboard."""

import asyncio
from datetime import date, timedelta
import json

import pytest
from fastapi import BackgroundTasks
from starlette.requests import Request

from routes import dashboard


def render_dashboard(ah, tmp_cache, monkeypatch):
    class Service:
        async def get_training(self, *args, **kwargs):return {'trains': []}
        async def get_body(self, *args, **kwargs):return {'records': []}

    context = {}
    template = dashboard.get_jinja_env().get_template('dashboard.html')
    class Template:
        blocks = template.blocks
        def new_context(self, values):
            context.update(values)
            return template.new_context(values)
    class Environment:
        def get_template(self, *args):return Template()

    monkeypatch.setattr(dashboard, 'get_data_service', Service)
    monkeypatch.setattr(dashboard, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(dashboard, '_ah', ah)
    monkeypatch.setattr(dashboard, 'get_jinja_env', Environment)
    response = asyncio.run(dashboard.dashboard_content(
        Request({'type': 'http', 'path': '/dashboard/content', 'headers': []}), BackgroundTasks()))
    return response, context


@pytest.mark.parametrize('bad', [None, 'bad', float('inf'), float('-inf')])
def test_bad_latest_hrv_keeps_other_metrics_and_missing_day_gap(
    isolated_health_db, tmp_cache, monkeypatch, bad
):
    ah = isolated_health_db
    today = date.today()
    rows = []
    for metric, day, value in [
        ('HeartRateVariabilitySDNN', today, bad),
        ('HeartRateVariabilitySDNN', today - timedelta(days=1), 42),
        ('HeartRateVariabilitySDNN', today - timedelta(days=8), 35),
        ('RestingHeartRate', today, 60), ('VO2Max', today, 40),
    ]:
        rows.append(dict(type='HKQuantityTypeIdentifier' + metric,
            start_date=day.isoformat() + ' 08:00:00 +0800',
            end_date=day.isoformat() + ' 08:01:00 +0800',
            value=value, unit='test', source_name='synthetic'))
    ah._bulk_insert(rows, [])
    original = ah.query_records()

    response, context = render_dashboard(ah, tmp_cache, monkeypatch)
    health = context['health']
    assert response.status_code == 200
    assert health['latest_hrv'] is None
    assert health['hrv_date'] == today.isoformat()
    assert health['hrv_trend'][-1]['value'] is None
    assert health['hrv_chg7'] is None
    assert health['latest_rhr'] == 60
    assert health['latest_vo2'] == 40
    json.dumps(health, allow_nan=False)
    assert ah.query_records() == original


def test_bad_dates_do_not_enter_trends_and_zero_remains_observed():
    records = [{'start_date': '2026-09-30 08:00:00 +0800', 'value': 0},
               {'start_date': '2026-09-31 08:00:00 +0800', 'value': 42},
               {'start_date': None, 'value': 42}]
    assert dashboard._build_trend(records) == [{'date': '09-30', 'iso_date': '2026-09-30', 'value': 0}]


def test_daily_and_weekly_averages_stay_finite_for_large_finite_samples():
    start = date(2026, 9, 1)
    records = [{'start_date': (start + timedelta(days=i)).isoformat(), 'value': 1e308}
               for i in range(8) for _ in range(2)]
    trend = dashboard._build_trend(records)
    assert all(point['value'] == 1e308 for point in trend)
    assert dashboard._ma7(trend)[-1] == 1e308
    assert dashboard._chg7(trend) == 0
    json.dumps(trend, allow_nan=False)


@pytest.mark.parametrize('train,rest,expected', [(0, 40, None), (40, 0, -100), (40, 20, -50), (.1, 1e308, None)])
def test_recovery_keeps_zero_and_handles_unknown_or_negative_comparisons(
    isolated_health_db, tmp_cache, monkeypatch, train, rest, expected
):
    ah = isolated_health_db
    today = date.today()
    ah._bulk_insert([dict(type='HKQuantityTypeIdentifierHeartRateVariabilitySDNN',
        start_date=day.isoformat()+' 08:00:00 +0800', end_date=day.isoformat()+' 08:01:00 +0800',
        value=value, unit='ms', source_name='synthetic')
        for day,value in [(today,train),(today-timedelta(days=1),rest)]], [])
    tmp_cache.set(f'training:{today.isoformat()}:True', {'trains':[{'datestr':today.isoformat(),'movements':[]}]})
    response, context = render_dashboard(ah, tmp_cache, monkeypatch)
    recovery = context['recovery']
    assert response.status_code == 200
    assert recovery['train_avg'] == train
    assert recovery['rest_avg'] == rest
    assert recovery['train_samples'] == recovery['rest_samples'] == 1
    assert recovery['rest_vs_train_pct'] == expected
    assert '高于训练日' not in response.body.decode()
    if expected is None:
        assert '与训练日对比未确认' in response.body.decode()
    json.dumps(recovery, allow_nan=False)


def test_unrepresentable_seven_day_change_stays_unknown():
    assert dashboard._chg7([
        {'iso_date': '2026-09-01', 'value': -1e308},
        {'iso_date': '2026-09-08', 'value': 1e308},
    ]) is None


def test_recovery_uses_exact_thirty_day_window_and_all_cached_training_days(
    isolated_health_db, tmp_cache, monkeypatch
):
    ah = isolated_health_db
    today = date.today()
    for offset in [20, 29, 30]:
        day = (today - timedelta(days=offset)).isoformat()
        tmp_cache.set(f'training:{day}:True', {'trains': [{'datestr': day, 'movements': []}]})
    ah._bulk_insert([dict(type='HKQuantityTypeIdentifierHeartRateVariabilitySDNN',
        start_date=(today-timedelta(days=offset)).isoformat()+' 08:00:00 +0800',
        end_date=(today-timedelta(days=offset)).isoformat()+' 08:01:00 +0800',
        value=value, unit='ms', source_name='synthetic')
        for offset,value in [(0,60),(20,40),(29,20),(30,999)]], [])
    response, context = render_dashboard(ah, tmp_cache, monkeypatch)
    recovery = context['recovery']
    assert response.status_code == 200
    assert recovery is not None
    assert len(recovery['chart_data']) == 3
    assert (recovery['train_days'], recovery['train_samples'], recovery['rest_samples']) == (2,2,1)
    assert recovery['train_avg'] == 30
    assert recovery['rest_avg'] == 60
    assert recovery['rest_vs_train_pct'] == 100


@pytest.mark.parametrize('latest', [60, None])
def test_recovery_latest_measurement_is_not_replaced_with_daily_average(
    isolated_health_db, tmp_cache, monkeypatch, latest
):
    ah = isolated_health_db
    day = date.today().isoformat()
    ah._bulk_insert([dict(type='HKQuantityTypeIdentifierHeartRateVariabilitySDNN',
        start_date=day+' '+clock+' +0800', end_date=day+' '+clock+' +0800',
        value=value, unit='ms', source_name='synthetic')
        for clock,value in [('08:00:00',40),('20:00:00',latest)]], [])
    tmp_cache.set(f'training:{day}:True', {'trains':[{'datestr':day,'movements':[]}]})
    response, context = render_dashboard(ah, tmp_cache, monkeypatch)
    assert response.status_code == 200
    assert context['recovery']['today_hrv'] == latest
    assert context['health']['latest_hrv'] == latest
    assert context['recovery']['train_avg'] == (50 if latest is not None else 40)
    assert context['recovery']['vs_train_pct'] == (20 if latest is not None else None)
