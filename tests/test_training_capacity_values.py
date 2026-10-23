"""Unrepresentable capacity is unknown; neighboring data and counts survive."""
import json
from datetime import date
import pytest
from analysis import summarize_training, training_volume_by_date, movement_history
from training_charts import build_chart_ranges


def train(sets, day='2026-10-22'):
    return {'datestr':day,'movements':[{'name':'卧推','sets':sets}]}


@pytest.mark.parametrize('sets', [
    [{'weight':1e200,'reps':1e200}],
    [{'weight':1e308,'reps':1},{'weight':1e308,'reps':1}],
])
def test_capacity_overflow_does_not_become_a_finite_or_infinite_total(sets):
    rows=[train(sets),train([{'weight':50,'reps':10}],'2026-10-21')]
    summary=summarize_training(rows)
    assert summary['total_volume'] is None
    assert summary['total_sets']==len(sets)+1
    assert training_volume_by_date(rows)=={'2026-10-22':None,'2026-10-21':500}
    hist=movement_history(rows,'卧推')
    assert hist[1]['total_volume'] is None
    assert hist[0]['total_volume']==500
    json.dumps(summary,allow_nan=False)
    json.dumps(hist,allow_nan=False)


def test_movement_one_rm_overflow_is_not_hidden_by_a_smaller_valid_set():
    hist,=movement_history([train([{'weight':1e200,'reps':1e200},{'weight':50,'reps':10}])],'卧推')
    assert hist['estimated_1rm'] is None
    assert hist['max_weight']==1e200
    assert hist['total_sets']==2


def test_weekly_and_period_sum_overflow_preserves_daily_samples():
    rows=[train([{'weight':1e308,'reps':1}],day) for day in ('2026-10-21','2026-10-22')]
    volumes,_=build_chart_ranges(rows,rows,date(2026,10,22))
    assert [entry['value'] for entry in volumes['30']]==[1e308,1e308]
    assert volumes['90']==[{'date':'2026-10-19','value':None}]
    assert summarize_training(rows)['total_volume'] is None
    json.dumps(volumes,allow_nan=False)


def test_weekly_unknown_day_cannot_be_summed_as_zero():
    rows=[train([{'weight':1e200,'reps':1e200}]),train([{'weight':50,'reps':10}],'2026-10-21')]
    volumes,_=build_chart_ranges(rows,rows,date(2026,10,22))
    assert volumes['90']==[{'date':'2026-10-19','value':None}]


def test_representable_sum_recovers_after_large_opposite_products():
    rows=[train([{'weight':1e308,'reps':2},{'weight':-1e308,'reps':2},{'weight':50,'reps':10}])]
    assert summarize_training(rows)['total_volume']==500
    assert movement_history(rows,'卧推')[0]['total_volume']==500


def test_zero_and_unfinished_overflow_do_not_create_unknown_capacity():
    rows=[train([{'weight':1e308,'reps':1e308,'done':False},{'weight':0,'reps':10}])]
    assert summarize_training(rows)['total_volume']==0
    assert summarize_training(rows)['total_sets']==1
    assert movement_history(rows,'卧推')[0]['total_volume']==0


@pytest.mark.parametrize('values,expected', [
    ([None,500],None), ([1e308,1e308],None),
    ([1e308,1e308,-1e308],1e308), ([2**53+1,-2**53],1), ([0,0],0),
])
def test_finite_sum_preserves_unknowns_and_representable_cancellation(values,expected):
    from health_values import finite_sum
    assert finite_sum(values)==expected


def test_exact_product_sum_recovers_tiny_terms_and_integer_cancellation():
    from health_values import finite_product_sum
    assert finite_product_sum([(5e-324,.5)]) is None
    assert finite_product_sum([(5e-324,.5),(5e-324,.5)])==5e-324
    assert finite_product_sum([(2**53+1,1),(-2**53,1)])==1
