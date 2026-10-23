const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const template = fs.readFileSync(path.join(__dirname, '../templates/health.html'), 'utf8');
const code = template.match(/let importPollTimer = null;[\s\S]*?(?=<\/script>)/)[0];
const scenario = process.argv[2];
const elements = {
  importBtn: {disabled:true, textContent:'开始后台导入', dataset:{exportAvailable:'false'}},
  syncBtn: {disabled:false, textContent:'立即同步 Hozz'},
  importStatus: {textContent:''}, importProgressBar: {style:{}},
  projectShutdownScreen: {hidden:true},
};
const requests = [], queue = [], timers = new Map(), events = {}, toasts = [];
let nextTimer = 0, reloads = 0;
const idle = (extra={}) => ({status:'idle', message:'', processed:0, total:null, export_available:false, ...extra});
const running = (extra={}) => idle({status:'running', kind:'xml', message:'正在导入', ...extra});
const ok = (extra={}) => idle({status:'ok', kind:'xml', message:'导入完成', total:0, ...extra});
const response = (data, status=200) => ({ok:status>=200 && status<300, status, json:async()=>data});
const later = () => {let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const pendingInitial = scenario==='stale-status' ? later() : null;
if (scenario==='legacy-service') {
  elements.importBtn.disabled=false; elements.importBtn.dataset.exportAvailable='true';
  queue.push(response({status:'idle',message:''}));
} else if (scenario==='terminal-on-load') queue.push(response(ok({total:12})));
else if (pendingInitial) queue.push(pendingInitial.promise);
else if (['network-retry','hozz-label','page-leave','back-cache'].includes(scenario)) queue.push(response(running({kind:'hozz',export_available:true})));
else queue.push(response(idle()));
const context = vm.createContext({
  document:{getElementById:id=>elements[id]||null},
  window:{addEventListener:(event,callback)=>{events[event]=callback;}},
  location:{reload:()=>{reloads++;}},
  showGlobalToast:(...args)=>toasts.push(args),
  fetch:async(url,options={})=>{
    requests.push({url,method:options.method||'GET'});
    const next=queue.shift()||response(idle());
    if(next instanceof Error) throw next;
    return await next;
  },
  setTimeout:(callback,delay)=>{timers.set(++nextTimer,{callback,delay});return nextTimer;},
  clearTimeout:id=>timers.delete(id),
});
const flush=()=>new Promise(setImmediate);
const scheduled=()=>Array.from(timers.values());
async function tick(delay) {
  const entry=Array.from(timers.entries()).find(([,timer])=>timer.delay===delay);
  assert.ok(entry,`Expected ${delay}ms retry/poll timer`);
  timers.delete(entry[0]);
  await entry[1].callback();
  await flush();
}
(async()=>{
  vm.runInContext(code, context);
  await flush();
  if(scenario==='start-status-fails') {
    queue.push(response({status:'started'}),response({},503));
    await context.startImport('/health/sync');
    assert.equal(elements.syncBtn.disabled,true);
    assert.equal(elements.importBtn.disabled,true);
    queue.push(response(ok({kind:'hozz'})));
    await tick(5000);
    assert.equal(elements.syncBtn.disabled,false);
    assert.equal(elements.importBtn.disabled,true);
  } else if(scenario==='terminal-on-load') {
    assert.equal(toasts.length,0); assert.equal(timers.size,0);
  } else if(scenario==='legacy-service') {
    assert.equal(elements.importBtn.disabled,false);
    assert.equal(elements.importBtn.textContent,'开始后台导入');
  } else if(scenario==='back-cache') {
    events.pagehide();
    queue.push(response(idle({export_available:true})));
    events.pageshow({persisted:true}); await flush();
    assert.equal(elements.importBtn.disabled,false);
    assert.equal(elements.syncBtn.disabled,false);
  } else if(scenario==='missing-file') {
    assert.equal(elements.importBtn.disabled,true);
    assert.equal(elements.importBtn.textContent,'开始后台导入');
    assert.equal(elements.syncBtn.disabled,false);
  } else if(scenario==='network-retry') {
    queue.push(new Error('offline'));
    await tick(1200);
    assert.equal(elements.importBtn.disabled,true);
    assert.equal(elements.syncBtn.disabled,true);
    queue.push(response(ok({kind:'hozz',export_available:true})));
    await tick(5000);
    assert.equal(elements.importBtn.disabled,false);
    assert.equal(elements.syncBtn.disabled,false);
  } else if(scenario==='hozz-label') {
    queue.push(response(ok({kind:'hozz',total:3,export_available:true})));
    await tick(1200);
    assert.ok(toasts.some(([message])=>message.includes('Hozz')));
    assert.ok(toasts.every(([message])=>!message.includes('Apple Health 导入完成')));
  } else if(scenario==='duplicate-start') {
    const post=later(); queue.push(post.promise);
    const first=context.startImport('/health/sync');
    const second=context.startImport('/health/sync');
    await flush();
    assert.equal(requests.filter(r=>r.method==='POST').length,1);
    assert.equal(elements.syncBtn.disabled,true);
    post.resolve(response({status:'started'}));
    queue.push(response(running({kind:'hozz'})));
    await Promise.all([first,second]);
  } else if(scenario==='stale-status') {
    const post=later();queue.push(post.promise);
    const started=context.startImport('/health/sync');
    pendingInitial.resolve(response(idle()));
    await flush();
    assert.equal(elements.syncBtn.disabled,true);
    assert.equal(elements.importBtn.disabled,true);
    post.resolve(response({status:'started'}));
    queue.push(response(running({kind:'hozz'})));
    await started;
  } else if(scenario==='http-error') {
    queue.push(response(ok(),503));
    await context.pollImportStatus();
    assert.ok(scheduled().some(timer=>timer.delay===5000));
    assert.match(elements.importStatus.textContent,/失败|重试/);
    assert.equal(toasts.length,0);
  } else if(scenario==='busy-not-owned') {
    queue.push(response({status:'busy'}),response(ok({kind:'hozz'})));
    await context.startImport('/health/sync');
    assert.ok(scheduled().every(timer=>timer.delay!==800));
    assert.equal(elements.importBtn.disabled,true);
  } else if(scenario==='page-leave') {
    assert.equal(typeof events.pagehide,'function');
    events.pagehide();
    assert.equal(timers.size,0);
  } else if(scenario==='source-disappears') {
    queue.push(response(running({export_available:true})),response(ok({export_available:false})));
    await context.pollImportStatus();
    await tick(1200);
    assert.equal(elements.importBtn.disabled,true);
    assert.equal(elements.syncBtn.disabled,false);
  } else if(scenario==='terminal-error-resets') {
    queue.push(response({status:'started'}),response(idle({status:'error',message:'failed'})));
    await context.startImport('/health/sync');
    queue.push(response(ok({kind:'hozz'})));
    await context.pollImportStatus();
    assert.equal(toasts.length,0);
    assert.ok(scheduled().every(timer=>timer.delay!==800));
  }
  assert.equal(reloads,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
