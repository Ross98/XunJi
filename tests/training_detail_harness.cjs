const fs=require('fs'),vm=require('vm'),assert=require('assert');
const scenario=process.argv[2],pending=[],events={},bodyEvents={};
function element(tag='div',date){const classes=new Set();return {tagName:tag.toUpperCase(),dataset:date?{date,trainingDate:date}:{},attributes:{},children:[],innerHTML:'initial',textContent:'',style:{},disabled:false,
 classList:{add:v=>classes.add(v),remove:v=>classes.delete(v),toggle(v,on){on?classes.add(v):classes.delete(v)},contains:v=>classes.has(v)},
 setAttribute(key,value){this.attributes[key]=value},replaceChildren(...nodes){this.children=nodes;this.innerHTML=''},
 closest(selector){return selector.includes('data-training-date')&&this.dataset.trainingDate?this:selector==='.cal-day'&&this.tagName==='TD'?this:null},
 matches(){return this.tagName!=='BUTTON'}};}
const detail=element(),cells=[element('td','2026-10-20'),element('td','2026-10-21')];
detail.dataset.date='2026-10-20';cells.forEach(e=>e.classList.add('cal-day'));
const context={AbortController,document:{getElementById:id=>detail,querySelectorAll:()=>cells,createElement:element,
 body:{addEventListener:(name,fn)=>(bodyEvents[name]??=[]).push(fn)}},
 window:{addEventListener:(name,fn)=>(events[name]??=[]).push(fn)},
 fetch(url,options={}){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});pending.push({url,options,resolve,reject});return promise;}};
vm.createContext(context);
let source=fs.readFileSync('templates/training.html','utf8');
let start=source.indexOf('let _detailRequestVersion');if(start<0)start=source.indexOf('async function selectTrainingDate');
source=source.slice(start,source.indexOf('// ── Movement tracker ──'));
vm.runInContext(source,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const select=date=>vm.runInContext(`selectTrainingDate(${JSON.stringify(date)})`,context);
async function respond(index,html){pending[index].resolve({ok:true,text:async()=>html});await flush();}
async function run(){
 if(scenario==='delegation'||scenario==='keyboard'||scenario==='space'){
  const event={target:cells[1],key:scenario==='space'?' ':'Enter',preventDefault(){}};
  for(const fn of bodyEvents[scenario==='delegation'?'click':'keydown']||[])fn(event);
  assert.strictEqual(pending.length,1);await respond(0,'day21');assert.strictEqual(detail.innerHTML,'day21');return;
 }
 select('2026-10-20');
 if(scenario==='failure'){
  pending[0].reject(new Error('<b>untrusted error</b>'));await flush();
  assert.strictEqual(detail.attributes['aria-busy'],'false');assert(!detail.innerHTML.includes('<b>untrusted error</b>'));
  assert(detail.children[0].textContent.includes('<b>untrusted error</b>'));select('2026-10-20');await respond(1,'recovered');assert.strictEqual(detail.innerHTML,'recovered');return;
 }
 if(scenario==='http_error'){pending[0].resolve({ok:false,status:503});await flush();assert.strictEqual(detail.attributes['aria-busy'],'false');assert(detail.children[0].textContent.includes('503'));return;}
 if(scenario==='invalid'){select('<b>bad</b>');await respond(0,'old');assert.strictEqual(pending.length,1);assert.notStrictEqual(detail.innerHTML,'old');assert.strictEqual(detail.attributes['aria-busy'],'false');return;}
 if(scenario==='same_date'){select('2026-10-21');select('2026-10-20');await respond(2,'new');await respond(0,'old');await respond(1,'old');assert.strictEqual(detail.innerHTML,'new');return;}
 if(scenario==='lifecycle'){
  for(const fn of events.pagehide||[])fn({});assert(pending[0].options.signal.aborted);
  await respond(0,'old');assert.notStrictEqual(detail.innerHTML,'old');
  for(const fn of events.pageshow||[])fn({persisted:true});assert.strictEqual(pending.length,2);await respond(1,'new');return;
 }
 if(scenario==='body'){
  let release;const body=new Promise(resolve=>release=resolve);pending[0].resolve({ok:true,text:()=>body});await flush();
  select('2026-10-21');await respond(1,'new');release('old');await flush();
 }else{
  select('2026-10-21');
  if(scenario==='busy'){await respond(0,'old');assert.strictEqual(detail.attributes['aria-busy'],'true');await respond(1,'new');}
  else {await respond(1,'new');if(scenario==='old_error'){pending[0].reject(new Error('old failure'));await flush();}else await respond(0,'old');}
 }
 assert.strictEqual(detail.innerHTML,'new');assert.strictEqual(detail.attributes['aria-busy'],'false');
 assert(pending[0].options.signal.aborted);
}
run().catch(error=>{console.error(error);process.exitCode=1;});
