import asyncio
import copy
import json
import pytest
from datetime import date
from starlette.requests import Request
from routes import body


@pytest.fixture
def render_body(isolated_health_db,tmp_cache,tmp_path,monkeypatch):
    class Today(date):
        @classmethod
        def today(cls):return cls(2026,10,23)
    records=[];context={}
    env=body.get_jinja_env()
    class Service:
        async def get_body(self,*args,**kwargs):return {'records':records}
    class Template:
        def render(self,**kwargs):
            context.update(kwargs)
            return env.get_template('body.html').render(**kwargs)
    class Templates:
        def get_template(self,name):return Template()
    monkeypatch.setattr(body,'date',Today)
    monkeypatch.setattr(body,'get_data_service',Service)
    monkeypatch.setattr(body,'get_cache',lambda:tmp_cache)
    monkeypatch.setattr(body,'get_jinja_env',Templates)
    monkeypatch.setattr(body,'BODY_CONFIG_PATH',str(tmp_path/'body.json'))
    def render(right,left,right_day='2026-10-23',left_day='2026-10-23'):
        records[:]=[{'type':'arm_right','value':right,'datestr':right_day},
                    {'type':'arm_left','value':left,'datestr':left_day}]
        before=copy.deepcopy(records)
        response=asyncio.run(body.body_page(Request({'type':'http','path':'/body','headers':[]}),range='all'))
        assert records==before
        return context,response.body.decode()
    return render


@pytest.mark.parametrize('right,left,percent',[(1e308,9e307,10.5),(30.04,30,0.1),(30.051,30,0.2),(30,28,6.9),(0,30,200),(30,30,0)])
def test_asymmetry_percentage_uses_raw_difference_without_overflow(render_body,right,left,percent):
    context,_=render_body(right,left)
    result,=context['asymmetry']
    assert result['percent']==percent
    json.dumps(result,allow_nan=False)


def test_tiny_real_difference_cannot_become_balance(render_body):
    context,html=render_body(30.04,30)
    result,=context['asymmetry']
    assert result['dominant']=='右侧'
    assert result['under_precision'] is True
    card=html.split('id="bodyAsymmetryCard"')[1].split('</script>')[0]
    assert '不足0.1' in card and '平衡' not in card.split('asymmetry-grid')[1]


def test_zero_pair_preserves_difference_but_not_a_fabricated_percentage(render_body):
    context,html=render_body(0,0)
    result,=context['asymmetry']
    assert result['difference']==0
    assert result['percent'] is None
    assert '差异比例未确认' in html


def test_same_day_bad_measurement_is_not_mislabeled_as_different_dates(render_body):
    context,html=render_body(-30,30)
    result,=context['asymmetry']
    assert result['difference'] is None
    assert result['percent'] is None
    assert '数值未确认，暂不比较' in html


def test_different_days_still_keep_both_values_without_comparison(render_body):
    context,html=render_body(30,28,left_day='2026-10-22')
    result,=context['asymmetry']
    assert result['difference'] is None and result['percent'] is None
    assert '日期不同，暂不比较' in html


def test_tiny_nonzero_percentage_is_not_displayed_as_zero(render_body):
    context,html=render_body(30.004,30)
    result,=context['asymmetry']
    assert result['percent_under_precision']
    assert '差异不足0.1%' in html


def test_bad_pair_does_not_hide_other_valid_limbs():
    from analysis import body_asymmetry
    latest={'arm_right':{'value':True,'date':'2026-10-23'},'arm_left':{'value':30,'date':'2026-10-23'},
            'leg_right':{'value':52,'date':'2026-10-23'},'leg_left':{'value':50,'date':'2026-10-23'}}
    result,=body_asymmetry(latest)
    assert result['label']=='大腿' and result['difference']==2
    assert latest['arm_right']['value'] is True


def test_percentage_matches_independent_decimal_ratio():
    from decimal import Decimal,localcontext
    from analysis import body_asymmetry
    for r,l in [(30.04,30),(1e308,9e307),(5e-324,1e-323),(100,1e-300)]:
        latest={'arm_right':{'value':r,'date':'2026-10-23'},'arm_left':{'value':l,'date':'2026-10-23'}}
        result,=body_asymmetry(latest)
        with localcontext() as context:
            context.prec=80
            right,left=Decimal.from_float(float(r)),Decimal.from_float(float(l))
            expected=round(float(abs(right-left)*200/(right+left)),1)
        assert result['percent']==expected
