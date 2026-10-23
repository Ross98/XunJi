"""Finite weighted observations must not produce infinite/zero overflow artifacts."""
import math
import json
import pytest
from analysis import summarize_set_metrics, heart_rate_trend


@pytest.mark.parametrize('values,weights,expected', [
    ([120,180],[8e307,8e307],150),
    ([120,180],[1e308,1e308],150),
    ([1e308,1e308],[2,3],1e308),
    ([1e-308,3e-308],[1e-308,1e-308],2e-308),
    ([120,180],[600,1800],165),
])
def test_weighted_heart_rate_survives_representable_inputs(values, weights, expected):
    sets = [{'avgHeartRate':value,'time':weight} for value,weight in zip(values,weights)]
    actual = summarize_set_metrics(sets)['avgHeartRate']
    assert actual is not None and math.isfinite(actual)
    assert math.isclose(actual,expected,rel_tol=1e-15,abs_tol=0)
    assert min(values) <= actual <= max(values)
    train = {'datestr':'2026-10-22','movements':[{'sets':sets}]}
    trend, = heart_rate_trend([train])
    assert trend['avg_hr'] == actual
    json.dumps(trend,allow_nan=False)


def test_missing_duration_keeps_multiple_heart_rates_unconfirmed():
    assert summarize_set_metrics([{'avgHeartRate':120,'time':600},{'avgHeartRate':180}])['avgHeartRate'] is None


@pytest.mark.parametrize('observations,expected', [
    ([(0,2),(0,3)],0),
    ([(2.1,5e-324),(2.1,5e-324)],2.1),
    ([(1e308,1e308),(-1e308,1e308),(1,1)],5e-309),
    ([(1e308,1e-308),(1e-308,1e308)],2e-308),
])
def test_weighted_helper_preserves_zero_and_extreme_cancellation(observations, expected):
    from health_values import health_weighted_mean
    value = health_weighted_mean(observations)
    assert value is not None and math.isclose(value,expected,rel_tol=1e-15,abs_tol=0)


@pytest.mark.parametrize('bad', [None, True, -1, 0, math.nan, math.inf, 'bad'])
def test_unknown_or_nonpositive_weight_cannot_be_silently_dropped(bad):
    from health_values import health_weighted_mean
    assert health_weighted_mean([(120,600),(180,bad)]) is None


def test_weighted_helper_has_no_evidence_for_empty_or_invalid_values():
    from health_values import health_weighted_mean
    assert health_weighted_mean([]) is None
    assert health_weighted_mean([(120,600),(math.inf,1800)]) is None


def test_weighted_mean_matches_independent_decimal_oracle_and_order_invariance():
    import random
    from decimal import Decimal, localcontext
    from health_values import health_weighted_mean
    for seed in range(100):
        rng = random.Random(seed)
        pairs = [(rng.uniform(1,9)*10.0**rng.randint(-300,300),
                  rng.uniform(1,9)*10.0**rng.randint(-300,300)) for _ in range(6)]
        with localcontext() as context:
            context.prec = 80  # Positive sums have no cancellation; far exceeds float precision.
            numerator = sum(Decimal(value)*Decimal(weight) for value,weight in pairs)
            denominator = sum(Decimal(weight) for _,weight in pairs)
            expected = float(numerator/denominator)
        actual = health_weighted_mean(pairs)
        reversed_value = health_weighted_mean(reversed(pairs))
        scaled = health_weighted_mean((value,weight*2) for value,weight in pairs)
        for value in (actual,reversed_value,scaled):
            assert value is not None and math.isfinite(value)
            assert math.isclose(value,expected,rel_tol=1e-14,abs_tol=0)


def test_nonzero_mean_below_float_range_is_unknown_not_a_fabricated_zero():
    from health_values import health_weighted_mean
    assert health_weighted_mean([(1e-200,1e-100),(0,1e308)]) is None
    assert health_weighted_mean([(1e-308,1e-308),(0,1e308)]) is None
