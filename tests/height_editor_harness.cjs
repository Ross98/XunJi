const fs = require('fs'), vm = require('vm'), assert = require('assert');
const elements = {}, pending = [], timers = [], toasts = [];
for (const id of ['heightEdit','heightInput','heightToggle','heightSave','heightCancel','heightStatus']) {
    elements[id] = {style: {display: id === 'heightEdit' ? 'none' : ''}, disabled: false,
        value: '170', defaultValue: '170', attributes: {}, textContent: '', hidden: true,
        setAttribute(k,v) { this.attributes[k] = v; }, focus() {}, select() {}};
}
let reloads = 0;
const context = {Number, document: {getElementById: id => elements[id]},
    showGlobalToast: (...args) => toasts.push(args), setTimeout: fn => timers.push(fn),
    location: {reload() {reloads++;}},
    fetch(url,options) {let resolve,reject;const promise = new Promise((a,b) => {resolve=a;reject=b;});
        pending.push({url,options,resolve,reject});return promise;}};
vm.createContext(context);
const source = fs.readFileSync('templates/body.html','utf8');
let start = source.indexOf('let _heightSaving');
if (start < 0) start = source.indexOf('function toggleHeightEdit()');
vm.runInContext(source.slice(start,source.indexOf('const historyRows')),context);
const flush = () => new Promise(resolve => setImmediate(resolve));
const invoke = name => context[name]();
async function run(scenario) {
    invoke('toggleHeightEdit');
    elements.heightInput.value = '180';
    if (scenario === 'cancel' || scenario === 'toggle') {
        invoke(scenario === 'cancel' ? 'cancelHeightEdit' : 'toggleHeightEdit');
        invoke('toggleHeightEdit');assert.strictEqual(elements.heightInput.value,'170');
        assert.strictEqual(elements.heightToggle.attributes['aria-expanded'],'true');return;
    }
    if (scenario === 'invalid') {
        elements.heightInput.value='99';invoke('saveHeight');await flush();
        assert.strictEqual(pending.length,0);assert(elements.heightStatus.textContent.includes('100'));return;
    }
    invoke('saveHeight');await flush();
    assert.strictEqual(JSON.parse(pending[0].options.body).height_cm,180);
    if (scenario === 'duplicate') {invoke('saveHeight');assert.strictEqual(pending.length,1);return;}
    if (scenario === 'busy') {
        for (const id of ['heightInput','heightSave','heightCancel','heightToggle']) assert(elements[id].disabled,id);
        invoke('cancelHeightEdit');invoke('toggleHeightEdit');
        assert.strictEqual(elements.heightEdit.style.display,'block');
        assert.strictEqual(elements.heightEdit.attributes['aria-busy'],'true');return;
    }
    if (scenario === 'transport') pending[0].reject(new Error('<b>unsafe</b>'));
    else if (scenario === 'http') pending[0].resolve({ok:false,status:503});
    else if (scenario === 'json') pending[0].resolve({ok:true,json:async()=>{throw new SyntaxError('bad JSON');}});
    else pending[0].resolve({ok:true,json:async()=>scenario==='null'?null:scenario==='application'?{status:'error',message:'synthetic disk full'}:{status:'ok'}});
    await flush();await flush();
    if (scenario === 'success') {
        assert.strictEqual(timers.length,1);assert(elements.heightSave.disabled);
        invoke('saveHeight');assert.strictEqual(pending.length,1);timers[0]();assert.strictEqual(reloads,1);return;
    }
    assert.strictEqual(timers.length,0);assert.strictEqual(reloads,0);
    assert.strictEqual(elements.heightInput.value,'180');
    assert.strictEqual(elements.heightStatus.hidden,false);
    assert.strictEqual(elements.heightStatus.attributes.role,'alert');
    for (const id of ['heightInput','heightSave','heightCancel','heightToggle']) assert(!elements[id].disabled,id);
    invoke('saveHeight');assert.strictEqual(pending.length,2);
}
run(process.argv[2]).catch(error=>{console.error(error);process.exitCode=1;});
