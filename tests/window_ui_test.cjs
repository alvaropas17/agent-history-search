/* Browser-free regressions for stale search and passage responses. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
class Element {
    constructor() { this.value = ''; this.children = []; this.hidden = false; this.textContent = ''; this.handlers = {}; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; this.textContent = ''; }
    addEventListener(name, handler) { this.handlers[name] = handler; }
    querySelector() { return null; }
    focus() {}
}
(async () => {
    const nodes = new Map(), pending = [], timers = [];
    const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
    get('host').value = 'all';
    const context = vm.createContext({
        URL, URLSearchParams, console,
        location: { hash: '#synthetic-token', search: '', pathname: '/', href: 'http://127.0.0.1:47620/' },
        sessionStorage: { setItem() {}, getItem() { return ''; } }, history: { replaceState() {} },
        window: {}, navigator: { clipboard: { async writeText() {} } },
        document: { getElementById: get, createElement: () => new Element(), createTextNode: text => ({ textContent: text }), addEventListener() {} },
        setTimeout: fn => { timers.push(fn); return timers.length; }, clearTimeout() {},
        fetch: (url, options) => {
            assert.equal(options.headers['X-Token'], 'synthetic-token');
            assert.equal(url.searchParams.has('t'), false);
            if (url.pathname === '/api/status') return Promise.resolve({ ok: true, json: async () => ({ demo: true, messages: {}, skipped: {}, indexing: false }) });
            return new Promise(resolve => pending.push({ url, resolve }));
        }
    });
    const html = fs.readFileSync(path.join(__dirname, '../agent_history/window.html'), 'utf8');
    vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], context);
    await new Promise(resolve => setImmediate(resolve));
    get('q').value = 'first';
    const firstSearch = vm.runInContext('search()', context);
    get('q').value = 'second';
    get('q').handlers.input();
    pending[0].resolve({ ok: true, json: async () => ({ results: [{ session: 'stale' }], words: ['first'] }) });
    await firstSearch;
    assert.equal(vm.runInContext('results.length', context), 0, 'Typing must invalidate an earlier search before debounce');
    const secondSearch = timers.shift()();
    assert.equal(pending[1].url.searchParams.get('q'), 'second');
    const rows = [
        { host: 'claude', session: 'first-session', text: 'first', ref: 'first-ref', role: 'user', timestamp: 0 },
        { host: 'claude', session: 'unsafe; command', text: 'second', ref: 'second-ref', role: 'user', timestamp: 0 }
    ];
    pending[1].resolve({ ok: true, json: async () => ({ results: rows, words: ['second'] }) });
    await secondSearch;
    const firstDetail = vm.runInContext('sel=0;openDetail()', context);
    const secondDetail = vm.runInContext('sel=1;openDetail()', context);
    pending[3].resolve({ ok: true, json: async () => ({ text: 'second passage', next_offset: null }) });
    await secondDetail;
    pending[2].resolve({ ok: true, json: async () => ({ text: 'stale first passage', next_offset: null }) });
    await firstDetail;
    assert.equal(vm.runInContext('curText', context), 'second passage', 'A slow passage must not overwrite the selected passage');
    assert.equal(get('b-resume').hidden, true, 'Unsafe session IDs must not form a shell command');
    const closingDetail = vm.runInContext('sel=0;openDetail()', context);
    vm.runInContext('closeDetail()', context);
    pending[4].resolve({ ok: true, json: async () => ({ text: 'closed passage', next_offset: null }) });
    await closingDetail;
    assert.equal(vm.runInContext('curText', context), '');
    console.log('Stale search, detail races, closed detail and safe resume command: OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
