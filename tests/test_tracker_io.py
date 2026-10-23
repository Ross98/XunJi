import asyncio
import threading
import httpx
import pytest
from fastapi import FastAPI
import routes
from routes import training


@pytest.fixture(autouse=True)
def isolated_tracker(tmp_cache,monkeypatch):
    tmp_cache.set('training:2026-10-22:True',{'trains':[{'datestr':'2026-10-22','movements':[{'name':'卧推','sets':[{'weight':50,'reps':10}]}]}]})
    monkeypatch.setattr(training,'get_cache',lambda:tmp_cache)


@pytest.mark.parametrize('stage',['keys','cache','merge','history'])
def test_tracker_work_runs_off_loop(stage,tmp_cache,monkeypatch):
    owner=threading.get_ident();threads=[]
    module,name={'keys':(tmp_cache,'keys_by_prefix'),'cache':(training,'get_cached_training'),
                 'merge':(training,'_merge_overlapping_trains'),'history':(training,'movement_history')}[stage]
    original=getattr(module,name)
    def observed(*args,**kwargs):
        threads.append(threading.get_ident())
        return original(*args,**kwargs)
    monkeypatch.setattr(module,name,observed)
    result=asyncio.run(training.training_track('卧推'))
    assert result['total_volume']==[500]
    assert threads and all(thread!=owner for thread in threads)


def test_tracker_loads_each_day_once(monkeypatch):
    original=training.get_cached_training;days=[]
    def observed(cache,day):
        days.append(day)
        return original(cache,day)
    monkeypatch.setattr(training,'get_cached_training',observed)
    assert asyncio.run(training.training_track('卧推'))['total_volume']==[500]
    assert days==['2026-10-22']


@pytest.mark.parametrize('reader_error',[False,True])
def test_slow_tracker_keeps_quit_responsive_and_joins_reader(monkeypatch,reader_error):
    entered,release,finished=threading.Event(),threading.Event(),threading.Event()
    original=training.get_cached_training
    def slow(*args,**kwargs):
        entered.set()
        try:
            assert release.wait(2)
            if reader_error:raise OSError('synthetic read failure')
            return original(*args,**kwargs)
        finally:finished.set()
    monkeypatch.setattr(training,'get_cached_training',slow)
    scheduled=[]
    monkeypatch.setattr(routes,'schedule_shutdown',lambda **kwargs:scheduled.append(kwargs))
    app=FastAPI();routes.register_routes(app)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',53000)),base_url='http://local') as client:
            pending=asyncio.create_task(client.get('/training/track/卧推'));drain=None
            try:
                assert await asyncio.to_thread(entered.wait,1)
                assert (await asyncio.wait_for(client.post('/admin/shutdown'),.5)).status_code==409
                assert not pending.done()
                assert (await client.post('/admin/shutdown',headers={'X-Xunji-Shutdown':'confirm'})).status_code==202
                draining=asyncio.Event()
                async def stop():
                    draining.set();await scheduled[0]['before_signal']()
                drain=asyncio.create_task(stop());await draining.wait();await asyncio.sleep(.02)
                assert not drain.done() and not finished.is_set()
            finally:
                release.set()
                if drain is not None:await drain
                response=await pending
                assert await asyncio.to_thread(finished.wait,1)
            assert response.status_code==503
            assert not app.state.shutdown_requests.tasks
    asyncio.run(run())
