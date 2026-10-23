import math
import json
import pytest
from analysis import body_stats,body_changes,calculate_bmi


def test_body_average_does_not_overflow_before_dividing():
    stats=body_stats([{'type':'weight','value':1e308},{'type':'weight','value':'1e308'}])
    assert stats['weight']=={'min':1e308,'max':1e308,'avg':1e308}
    json.dumps(stats,allow_nan=False)


@pytest.mark.parametrize('bad',[True,math.inf,math.nan,'bad',10**1000],ids=['boolean','infinity','nan','text','overflow_int'])
def test_bad_body_sample_does_not_poison_valid_statistics(bad):
    assert body_stats([{'type':'weight','value':bad},{'type':'weight','value':70}])['weight']=={'min':70,'max':70,'avg':70}


def test_overflowing_daily_change_is_unknown_but_next_change_survives():
    rows=[{'type':'weight','datestr':day,'value':value} for day,value in
          [('2026-10-20',-1e308),('2026-10-21',1e308),('2026-10-22',1e308)]]
    changes=body_changes(rows)
    assert changes=={'weight:2026-10-21':None,'weight:2026-10-22':0}
    json.dumps(changes,allow_nan=False)


@pytest.mark.parametrize('weight,height,expected',[
    (70.5,170,24.4), (0,170,0), (1e308,1e308,0.0),
    (1e-308,1e-200,1e96), (1e308,1e-200,None),
    (True,170,None),(70,True,None),(math.inf,170,None),(70,math.inf,None),
    (70,math.nan,None),(10**1000,170,None),(70,0,None),(-70,170,None),
])
def test_bmi_guards_inputs_and_intermediate_arithmetic(weight,height,expected):
    actual=calculate_bmi(weight,height)
    if expected is None:assert actual is None
    else:assert actual is not None and math.isclose(actual,expected,rel_tol=1e-15,abs_tol=0)


@pytest.mark.parametrize('metric',['weight','bodyfat'])
def test_body_page_keeps_overflowed_changes_out_of_display_payload(
    metric,isolated_health_db,tmp_cache,tmp_path,monkeypatch
):
    import asyncio
    from starlette.requests import Request
    from routes import body
    records=[{'type':metric,'datestr':day,'value':value} for day,value in
             [('2026-10-20',-1e308),('2026-10-21',1e308)]]
    class Service:
        async def get_body(self,*args,**kwargs):return {'records':records}
    context={}
    class Template:
        def render(self,**kwargs):context.update(kwargs);return 'ok'
    class Templates:
        def get_template(self,name):return Template()
    monkeypatch.setattr(body,'get_data_service',Service)
    monkeypatch.setattr(body,'get_cache',lambda:tmp_cache)
    monkeypatch.setattr(body,'BODY_CONFIG_PATH',str(tmp_path/'body.json'))
    monkeypatch.setattr(body,'get_jinja_env',Templates)
    asyncio.run(body.body_page(Request({'type':'http','headers':[]}),range='all'))
    assert context['period_weight_change' if metric=='weight' else 'period_bf_change'] is None
    assert context['latest_weight_change' if metric=='weight' else 'latest_bf_change'] is None
    json.dumps(context['records_data'],allow_nan=False)
    assert records[0]['value']==-1e308


def test_body_stats_skips_bad_rows_and_preserves_true_zero():
    assert body_stats([None,[],{'type':[],'value':70},{'type':'weight','value':0}])=={'weight':{'min':0,'max':0,'avg':0}}


def test_synthetic_rounding_boundary_is_independent_of_record_order():
    from itertools import permutations
    values=[70.03,70.04,70.01,70.02]
    for ordering in permutations(values):
        records=[{'type':'weight','value':value} for value in ordering]
        assert body_stats(records)['weight']['avg']==70.03
