"""Readiness compares real finite values before display rounding."""
import math
import pytest
from planner import generate_plan


@pytest.mark.parametrize('recovery,score,reason', [
    ({'hrv': 0, 'hrv_avg': 40}, 70, 'HRV 低于30日基线'),
    ({'hrv': 31.6, 'hrv_avg': 40}, 70, 'HRV 低于30日基线'),
    ({'sleep_hours': 5.49}, 65, '睡眠明显不足'),
    ({'rhr': 60.6, 'rhr_avg': 52.8}, 90, '静息心率略高'),
])
def test_readiness_uses_unrounded_measurements(recovery, score, reason):
    result = generate_plan([], recovery=recovery)['readiness']
    assert result['score'] == score
    assert reason in result['reasons']


@pytest.mark.parametrize('value', [math.nan, math.inf, -math.inf, -1, True, 'bad', [], {}])
@pytest.mark.parametrize('field,other', [('sleep_hours', {}), ('hrv', {'hrv_avg': 40}),
                                       ('rhr', {'rhr_avg': 60})])
def test_invalid_recovery_value_never_becomes_positive_evidence(value, field, other):
    result = generate_plan([], recovery={field: value, **other})['readiness']
    assert result['score'] is None
    assert result['status'] == '数据不足'


@pytest.mark.parametrize('recovery', [[], ['bad'], 'bad', True])
def test_wrong_recovery_container_is_missing_evidence(recovery):
    assert generate_plan([], recovery=recovery)['readiness']['score'] is None


@pytest.mark.parametrize('baseline', [0, -1, math.inf, math.nan, True, 'bad'])
@pytest.mark.parametrize('metric', ['hrv', 'rhr'])
def test_nonpositive_or_invalid_baseline_cannot_supply_a_comparison(metric, baseline):
    result = generate_plan([], recovery={metric: 40, metric+'_avg': baseline})['readiness']
    assert result['score'] is None


@pytest.mark.parametrize('flag', ['sleep_conflict', 'sleep_time_unconfirmed'])
def test_sleep_uncertainty_cannot_supply_a_score(flag):
    assert generate_plan([], recovery={'sleep_hours': 8, flag: True})['readiness']['score'] is None


def test_valid_evidence_survives_an_invalid_neighbor():
    result = generate_plan([], recovery={'sleep_hours': 8, 'hrv': math.nan, 'hrv_avg': 40})['readiness']
    assert result['score'] == 100
    assert result['reasons'] == ['睡眠充足']


def test_plan_http_renders_precise_score_with_existing_display_format(
    isolated_health_db, tmp_cache, tmp_path, monkeypatch
):
    import asyncio
    from datetime import date
    import httpx
    from app import app
    from routes import plan
    class Today(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 22)
    monkeypatch.setattr(plan, 'date', Today)
    monkeypatch.setattr(plan, 'get_cache', lambda: tmp_cache)
    monkeypatch.setattr(plan, 'PLAN_CONFIG_PATH', tmp_path/'plan.json')
    trains = [{'datestr': '2026-10-21', 'movements': [{'name': '卧推', 'sets': [{}]}]}]
    tmp_cache.set('training:2026-10-21:True', {'trains': trains})
    with isolated_health_db._conn() as conn:
        for day, value in [('2026-10-21', 48.4), ('2026-10-22', 31.6)]:
            stamp = day+' 08:00:00 +0800'
            conn.execute('INSERT INTO health_records(type,start_date,end_date,value) VALUES(?,?,?,?)',
                         ('HKQuantityTypeIdentifierHeartRateVariabilitySDNN',stamp,stamp,value))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response = await client.get('/plan')
        assert response.status_code == 200
        assert '<strong>70</strong>' in response.text
        assert 'HRV 低于30日基线' in response.text
        assert 'HRV <b>32.0</b>' in response.text
    asyncio.run(run())
