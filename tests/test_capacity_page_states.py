import asyncio
import json
import shutil
import subprocess
import httpx
from app import app
from routes import training


def test_overflow_history_api_and_detail_preserve_unknown_values(tmp_cache,monkeypatch):
    rows=[{'datestr':'2026-10-22','movements':[{'name':'卧推','sets':[{'weight':1e200,'reps':1e200}]}]}]
    tmp_cache.set('training:2026-10-22:True',{'trains':rows})
    class Service:
        async def get_training(self,*args,**kwargs):return {'trains':rows}
    monkeypatch.setattr(training,'get_cache',lambda:tmp_cache)
    monkeypatch.setattr(training,'get_data_service',Service)
    monkeypatch.setattr(training,'_ensure_training_history_backfill',lambda *args:None)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://test') as client:
            history=await client.get('/training/track/卧推')
            assert history.status_code==200
            assert history.json()['total_volume']==[None]
            assert history.json()['estimated_1rm']==[None]
            json.dumps(history.json(),allow_nan=False)
            detail=await client.get('/training/2026-10-22')
            assert detail.status_code==200
            assert '总容量未确认' in detail.text
    asyncio.run(run())
    assert tmp_cache.get('training:2026-10-22:True')=={'trains':rows}


def test_shipped_capacity_js_never_interprets_null_as_zero():
    script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('templates/training.html','utf8');
const vol=source.slice(source.indexOf('function renderVolume(range)'),source.indexOf("document.querySelectorAll('#tab-volume .btn-range')"));
const track=source.slice(source.indexOf('function renderTrack(data)'),source.indexOf('function loadTrack(movement)'));
function context(){
 const canvas={style:{}},summary={innerHTML:''},state={hidden:true,textContent:''};
 const c={volData:{},window:{},document:{getElementById:id=>id==='volumeSummary'?summary:canvas},
 markRangeButtons(){},Chart:function(ctx,config){this.config=config;this.destroy=()=>{};},
 trackCtx:canvas,trackSummary:summary,trackState:state,_trackChart:null};
 vm.createContext(c);vm.runInContext(vol+'\n'+track,c);return {c,canvas,summary,state};
}
let t=context();t.c.volData={'30':[{date:'a',value:null},{date:'b',value:500}]};t.c.renderVolume(30);
assert(t.summary.innerHTML.includes('未确认'));
assert(!t.summary.innerHTML.includes('<b>250</b>'));
assert(t.summary.innerHTML.includes('<b>500</b>'));
assert.strictEqual(t.c.window._volChart.config.data.datasets[0].data[0],null);
t=context();t.c.volData={'30':[{date:'a',value:null}]};t.c.renderVolume(30);
assert(t.summary.innerHTML.includes('<b>--</b>'));
t=context();t.c.volData={'30':[{date:'a',value:1e308},{date:'b',value:1e308}]};t.c.renderVolume(30);
assert(!t.summary.innerHTML.includes('Infinity'));
assert(t.summary.innerHTML.includes('1e+308'));
t=context();t.c.renderTrack({dates:['a','b'],max_weight:[50,1e200],total_volume:[500,null],total_sets:[1,2],estimated_1rm:[60,null]});
assert(t.summary.innerHTML.includes('<b>--</b>kg 估算1RM'));
assert.strictEqual(t.state.hidden,false);
assert(t.state.textContent.includes('未确认'));
t=context();t.c.renderTrack({dates:['a'],max_weight:[0],total_volume:[0],total_sets:[1],estimated_1rm:[0]});
assert(t.summary.innerHTML.includes('<b>0.0</b>kg 估算1RM'));
assert.strictEqual(t.state.hidden,true);
'''
    result=subprocess.run([shutil.which('node') or '/opt/homebrew/bin/node','-e',script],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
