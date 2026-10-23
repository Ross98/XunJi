const fs = require('fs'), vm = require('vm'), assert = require('assert');
const pending = [], events = {}, bodyEvents = {};
const makeElement = () => ({ attributes: {}, children: [], style: {}, textContent: '', innerHTML: '',
    setAttribute(key, value) { this.attributes[key] = value; },
    replaceChildren(...nodes) { this.children = nodes; this.innerHTML = ''; } });
const calendar = makeElement(), status = makeElement();
let selectionSyncs = 0;
const context = { AbortController,
    syncTrainingDateSelection() { selectionSyncs++; },
    document: { getElementById: id => id === 'calendar-container' ? calendar : status,
        createElement: makeElement, body: { addEventListener: (name, fn) => bodyEvents[name] = fn } },
    window: { addEventListener: (name, fn) => events[name] = fn },
    fetch(url, options) { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; });
        pending.push({ url, options, resolve, reject }); return promise; }
};
vm.createContext(context);
const source = fs.readFileSync('templates/training.html', 'utf8');
const start = source.indexOf('// ── Calendar month requests ──');
assert(start >= 0, 'calendar month requests need a shared latest-selection controller');
vm.runInContext(source.slice(start, source.indexOf('// ── Movement tracker ──')), context);
const select = url => context.selectTrainingMonth(url);
const flush = () => new Promise(resolve => setImmediate(resolve));
const respond = async (i, text) => { pending[i].resolve({ ok: true, text: async () => text }); await flush(); };
const url = month => '/training/calendar/2026/' + month;
async function run(scenario) {
    if (scenario === 'delegation') {
        let prevented = false;
        bodyEvents.click({ target: { closest: () => ({ dataset: { calendarUrl: url(11) }, disabled: false }) }, preventDefault() { prevented = true; } });
        assert(prevented); assert.strictEqual(pending[0].url, url(11)); await respond(0, 'November'); return;
    }
    select(url(9));
    if (scenario === 'failure' || scenario === 'http_error') {
        if (scenario === 'failure') pending[0].reject(new Error('<b>untrusted</b>'));
        else pending[0].resolve({ ok: false, status: 503 });
        await flush(); assert.strictEqual(calendar.attributes['aria-busy'], 'false');
        assert.strictEqual(calendar.innerHTML, ''); assert(status.textContent.includes('重试'));
        assert(!status.innerHTML.includes('<b>')); assert.strictEqual(status.attributes.role, 'alert');
        select(url(9)); await respond(1, 'retry'); assert.strictEqual(calendar.innerHTML, 'retry'); assert.strictEqual(status.textContent, ''); return;
    }
    if (scenario === 'invalid') {
        for (const bad of ['/training/calendar/0/1', '/training/calendar/10000/1', '/training/calendar/2026/13', 'https://other.invalid/']) select(bad);
        assert.strictEqual(pending.length, 1); await respond(0, 'stale'); assert.notStrictEqual(calendar.innerHTML, 'stale'); return;
    }
    if (scenario === 'lifecycle') {
        events.pagehide(); assert(pending[0].options.signal.aborted); await respond(0, 'stale');
        assert.notStrictEqual(calendar.innerHTML, 'stale'); events.pageshow({ persisted: true });
        assert.strictEqual(pending.length, 2); await respond(1, 'restored'); assert.strictEqual(calendar.innerHTML, 'restored'); return;
    }
    let release;
    if (scenario === 'body') { pending[0].resolve({ ok: true, text: () => new Promise(resolve => release = resolve) }); await flush(); }
    select(url(11));
    assert(pending[0].options.signal.aborted);
    if (scenario === 'busy') { await respond(0, 'old'); assert.strictEqual(calendar.attributes['aria-busy'], 'true'); }
    if (scenario === 'same_month') { select(url(9)); await respond(2, 'latest'); await respond(1, 'old'); }
    else await respond(1, 'latest');
    if (scenario === 'body') { release('old'); await flush(); }
    else if (scenario === 'old_error') { pending[0].reject(new Error('old')); await flush(); }
    else if (scenario !== 'busy') await respond(0, 'old');
    assert.strictEqual(calendar.innerHTML, 'latest'); assert.strictEqual(selectionSyncs, 1);
    assert.strictEqual(calendar.attributes['aria-busy'], 'false'); assert.strictEqual(status.textContent, '');
}
run(process.argv[2]).catch(error => { console.error(error); process.exitCode = 1; });
