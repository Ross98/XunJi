const fs=require('fs'),vm=require('vm'),assert=require('assert');
const scenario=process.argv[2];
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
function element(){return {style:{},dataset:{},hidden:true,value:'',textContent:'',innerHTML:'',children:[],handlers:{},
 append(...nodes){this.children.push(...nodes)},appendChild(node){this.children.push(node)},replaceChildren(){this.children=[]},
 addEventListener(name,fn){(this.handlers[name]??=[]).push(fn)},fire(name,event={}){for(const fn of this.handlers[name]||[])fn.call(this,event)}};}
const elements=Object.fromEntries(['movementSearch','favoriteMovement','trackChart','trackState','trackSummary','movementShortcuts'].map(name=>[name,element()]));
const stored=new Map();
const bad={object:'{}',null:'null',string:'"bad"',mixed:'[null,7,{},"卧推","卧推"]'};
if(bad[scenario])stored.set('xunji_favorite_movements',bad[scenario]);
const blocked=scenario==='blocked',writeBlocked=blocked||scenario==='write_blocked';
const requests=[],charts=[],events={};
const context={AbortController,document:{getElementById:id=>elements[id],createElement:element,createTextNode:text=>({textContent:text})},
 localStorage:{getItem(key){if(blocked)throw new Error('storage unavailable');return stored.get(key)??null},setItem(key,value){if(writeBlocked)throw new Error('storage unavailable');stored.set(key,value)}},
 window:{addEventListener(name,fn){(events[name]??=[]).push(fn)}},
 fetch(url,options={}){const request={url,options,...deferred()};requests.push(request);return request.promise},
 Chart:function(ctx,config){charts.push(config);this.destroy=()=>{};}};
vm.createContext(context);
let source=fs.readFileSync('templates/training.html','utf8');
source=source.slice(source.indexOf('// ── Movement tracker ──'),source.lastIndexOf('</script>'));
source=source.replace('{{ movement_names | tojson | safe }}',JSON.stringify(['卧推','深蹲']));
vm.runInContext(source,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const data=weight=>({dates:['2026-10-22'],max_weight:[weight],total_volume:[weight*10],total_sets:[1],estimated_1rm:[weight*1.2]});
async function respond(index,weight){requests[index].resolve({ok:true,json:async()=>data(weight)});await flush();}
function select(name){elements.movementSearch.value=name;vm.runInContext('loadTrack(movementSearch.value)',context);}
async function run(){
 if(bad[scenario]){assert.strictEqual(stored.get('xunji_favorite_movements'),bad[scenario]);const favorites=JSON.parse(vm.runInContext('JSON.stringify(getFavoriteMovements())',context));assert(Array.isArray(favorites));assert(favorites.every(value=>typeof value==='string'));assert.strictEqual(new Set(favorites).size,favorites.length);await respond(0,50);return;}
 if(scenario==='blocked'||scenario==='write_blocked'){await respond(0,50);elements.favoriteMovement.fire('click');assert.strictEqual(elements.favoriteMovement.textContent,'★');assert(elements.trackState.textContent.includes('本次页面'));return;}
 if(scenario==='current_error'||scenario==='http_error'||scenario==='current_json_error'){
 await respond(0,50);select('深蹲');
 if(scenario==='current_error')requests[1].reject(new Error('current error'));
 else if(scenario==='http_error')requests[1].resolve({ok:false,status:503});
 else requests[1].resolve({ok:true,json:async()=>{throw new Error('invalid current JSON')}});
 await flush();assert.strictEqual(elements.trackSummary.innerHTML,'');assert.strictEqual(elements.trackState.hidden,false);assert(elements.trackState.textContent.includes('读取失败'));assert(elements.trackState.textContent.includes('重试'));select('深蹲');await respond(2,100);assert.strictEqual(elements.trackState.hidden,true);return;
 }
 if(scenario==='cleanup'){select('深蹲');requests[0].reject(new Error('old error'));await flush();select('卧推');assert(requests[1].options.signal.aborted);await respond(2,150);await respond(1,100);assert.strictEqual(charts.at(-1).data.datasets[0].data[0],150);return;}
 if(scenario==='same_name'){select('深蹲');select('卧推');await respond(2,150);await respond(0,50);await respond(1,100);assert.strictEqual(charts.at(-1).data.datasets[0].data[0],150);return;}
 if(scenario==='input'){elements.movementSearch.value='尚未选择';elements.movementSearch.fire('input');await respond(0,50);assert.strictEqual(elements.trackSummary.innerHTML,'');assert.strictEqual(charts.length,0);return;}
 if(scenario==='invalid'){select('');await respond(0,50);assert.strictEqual(elements.trackSummary.innerHTML,'');assert.strictEqual(charts.length,0);return;}
 if(scenario==='lifecycle'){for(const fn of events.pagehide||[])fn({});assert(requests[0].options.signal.aborted);await respond(0,50);assert.strictEqual(charts.length,0);for(const fn of events.pageshow||[])fn({persisted:false});assert.strictEqual(requests.length,1);for(const fn of events.pageshow||[])fn({persisted:true});assert.strictEqual(requests.length,2);await respond(1,100);assert.strictEqual(charts.length,1);return;}
 if(scenario==='loading'){await respond(0,50);select('深蹲');assert.strictEqual(elements.trackSummary.innerHTML,'');assert.strictEqual(elements.trackChart.style.display,'none');await respond(1,100);return;}
 if(scenario==='json'){
   const body=deferred();requests[0].resolve({ok:true,json:()=>body.promise});await flush();select('深蹲');await respond(1,100);body.resolve(data(50));await flush();
 }else{
   select('深蹲');await respond(1,100);
   if(scenario==='error'){requests[0].reject(new Error('old error'));await flush();}
   else await respond(0,50);
 }
 assert.strictEqual(charts.at(-1).data.datasets[0].data[0],100);
 assert(!elements.trackState.textContent.includes('old error'));
 assert(requests[0].options.signal.aborted);
}
run().catch(error=>{console.error(error);process.exitCode=1;});
