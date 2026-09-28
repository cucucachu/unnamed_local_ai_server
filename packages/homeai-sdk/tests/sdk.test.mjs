// The runtime (dev build) and the fixture app in jsdom, over the WebView
// transport (`ReactNativeWebView.postMessage` / `__homeaiReceive`), with the
// real host library in front of a stand-in platform (helpers.mjs).
//   npm test    (builds dist/ first)
import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import { JSDOM } from 'jsdom';
import { ALLOWED_MODULES, SDK_TYPES } from '../build.mjs';
import { APP_ID, INSTANCE_ID, RUNTIME_DEV, bundleApp, importHost, stubPlatform } from './helpers.mjs';

const SPACE = { id: '33333333-3333-4333-8333-333333333333', slug: 'family', name: 'Family', role: 'editor' };
const runtimeCode = fs.readFileSync(RUNTIME_DEV, 'utf8');

async function until(what, fn, timeoutMs = 3000) {
  const t0 = Date.now();
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() - t0 > timeoutMs) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, 5));
  }
}

/** A booted sandbox: `$(testID)`, `text(testID)`, `click(testID)`, `type(testID, s)`. */
async function boot({ config = { initialPath: '/', space: SPACE }, readOnly = false, edits } = {}) {
  const host = await importHost();
  const platform = stubPlatform();
  const dom = new JSDOM('<!doctype html><html><head></head><body><div id="root"></div></body></html>', {
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    url: 'about:blank',
  });
  const win = dom.window;
  win.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  const events = [];
  const wire = [];
  const toSandbox = [];
  let bundleCode = await bundleApp(undefined, { edits });
  const bridge = host.createBridgeHost({
    send: (s) => (toSandbox.push(JSON.parse(s)), win.__homeaiReceive(s)),
    forward: host.platformForward(INSTANCE_ID, { fetch: platform.fetch(async () => bundleCode) }),
    readOnly,
    onEvent: (event, data) => events.push({ event, data }),
  });
  win.ReactNativeWebView = {
    postMessage(s) {
      wire.push(JSON.parse(s));
      bridge.receive(s);
    },
  };
  win.__homeai_config = config;
  const stopRelay = platform.onEvent(
    host.platformEventRelay({
      instanceId: INSTANCE_ID,
      appId: APP_ID,
      host: bridge,
      reload: async () => (await host.fetchBundle(INSTANCE_ID, { fetch: platform.fetch(async () => bundleCode) })).code,
    }),
  );
  const ctx = dom.getInternalVMContext();
  new vm.Script(runtimeCode, { filename: 'runtime.js' }).runInContext(ctx);
  new vm.Script(bundleCode, { filename: 'app.js' }).runInContext(ctx);
  const $ = (id) => [...win.document.querySelectorAll(`[data-testid="${id}"]`)].find((el) => !el.closest('[style*="display: none"]')) ?? null;
  const text = (id) => $(id)?.textContent ?? null;
  const s = {
    win,
    host,
    bridge,
    platform,
    events,
    wire,
    toSandbox,
    $,
    text,
    until,
    requests: (method) => wire.filter((m) => m.kind === 'req' && (!method || m.method === method)),
    click: (id) => $(id).click(),
    type(id, value) {
      const el = $(id);
      Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, 'value').set.call(el, value);
      el.dispatchEvent(new win.Event('input', { bubbles: true }));
    },
    setBundle: async (edits2) => (bundleCode = await bundleApp(undefined, { edits: edits2 })),
    close() {
      stopRelay();
      bridge.close();
      win.close();
    },
  };
  await until('first render', () => events.some((e) => e.event === 'runtime.ready') && text('count') === '0 items');
  return s;
}

test('renders the index from useQuery, with the space from the config', async () => {
  const s = await boot();
  assert.equal(s.text('space'), 'Family (editor)');
  assert.equal(s.text('build'), 'v1');
  assert.deepEqual(s.requests('db.getAll')[0].params, { sql: 'SELECT id, name, done FROM items ORDER BY id', params: [] });
  assert.deepEqual(s.platform.calls[0], { op: 'getAll', sql: 'SELECT id, name, done FROM items ORDER BY id', params: [] });
  s.close();
});

test('runAsync writes through the bridge and useQuery shows it at once', async () => {
  const s = await boot();
  s.type('new-item', 'Milk');
  await until('input', () => s.$('new-item').value === 'Milk');
  s.click('add');
  await until('row', () => s.text('count') === '1 items' && s.text('item-1') === 'Milk');
  assert.equal(s.text('status'), 'added 1');
  assert.deepEqual(s.requests('db.run')[0].params, { sql: 'INSERT INTO items (name) VALUES (?)', params: ['Milk'] });
  assert.deepEqual(s.platform.db.prepare('SELECT name FROM items').all().map((r) => ({ ...r })), [{ name: 'Milk' }]);
  s.close();
});

test('useQuery re-runs on db.changed from a write elsewhere, coalescing bursts', async () => {
  const s = await boot();
  const before = s.requests('db.getAll').length;
  s.platform.externalWrite("INSERT INTO items (name) VALUES ('Eggs')");
  await until('external row', () => s.text('item-1') === 'Eggs');
  const afterOne = s.requests('db.getAll').length;
  assert.equal(afterOne, before + 1);
  for (let i = 0; i < 10; i++) s.platform.externalWrite('INSERT INTO items (name) VALUES (?)', [`x${i}`]);
  await until('burst', () => s.text('count') === '11 items');
  await new Promise((r) => setTimeout(r, 30));
  const burst = s.requests('db.getAll').length - afterOne;
  assert.ok(burst >= 1 && burst <= 3, `${burst} queries for 10 changes`);
  s.close();
});

test('runAction runs the named action and refreshes queries', async () => {
  const s = await boot();
  s.platform.externalWrite("INSERT INTO items (name) VALUES ('a'), ('b')");
  await until('rows', () => s.text('count') === '2 items');
  s.click('mark-all');
  await until('action', () => s.text('status') === '2 marked, 2 done');
  await until('refresh', () => s.text('item-1') === 'a ✓' && s.text('item-2') === 'b ✓');
  assert.deepEqual(s.requests('action')[0].params, { name: 'markAllDone', params: {} });
  s.close();
});

test('Link, params, getFirstAsync (variadic and named binds), back, nav.changed', async () => {
  const s = await boot();
  s.platform.externalWrite("INSERT INTO items (name) VALUES ('Eggs')");
  await until('row', () => s.text('item-1') === 'Eggs');
  s.click('item-1');
  await until('detail', () => s.text('detail-name') === 'Eggs');
  assert.equal(s.text('detail-status'), 'Not done');
  const headings = () => [...s.win.document.querySelectorAll('[role="heading"]')].map((h) => h.textContent);
  await until('in-screen Stack.Screen title', () => headings().includes('Eggs'));
  assert.deepEqual(headings(), ['Runtime check', 'Eggs']);
  assert.deepEqual(s.requests('db.getFirst')[0].params, { sql: 'SELECT id, name, done FROM items WHERE id = ?', params: [1] });
  assert.ok(s.requests('db.getAll').some((r) => JSON.stringify(r.params.params) === '{"$id":1}'));
  s.click('toggle');
  await until('toggled', () => s.text('detail-status') === 'Done');
  s.click('back');
  await until('index', () => s.text('item-1') === 'Eggs ✓');
  assert.deepEqual(s.events.filter((e) => e.event === 'nav.changed').map((e) => e.data.path), ['/', '/item/1', '/']);
  s.close();
});

test('a space event re-renders useSpace', async () => {
  const s = await boot({ config: { initialPath: '/' } });
  assert.equal(s.text('space'), 'no space');
  s.bridge.setSpace({ ...SPACE, name: 'Home', role: 'owner' });
  await until('space', () => s.text('space') === 'Home (owner)');
  s.close();
});

test('SQL errors surface as useQuery.error; viewers are refused writes by the host', async () => {
  const index = fs.readFileSync(new URL('./fixtures/runtime-check/app/index.tsx', import.meta.url), 'utf8');
  const s = await boot({ readOnly: true, edits: { 'app/index.tsx': index.replace("{data ? `${data.length} items` : 'loading'}", "{data ? `${data.length} items` : error ? '0 items' : 'loading'}").replace('FROM items ORDER BY id', 'FROM nope') } });
  assert.equal(s.text('error'), 'no such table: nope');
  s.type('new-item', 'Milk');
  await until('input', () => s.$('new-item').value === 'Milk');
  s.click('add');
  await until('refusal', () => s.text('status') === 'refused: read_only');
  assert.equal(s.platform.calls.filter((c) => c.op === 'run').length, 0);
  s.close();
});

test('app_built for this app delivers its new bundle as bundle.load (evaluated in the browser test)', async () => {
  const s = await boot();
  const index = fs.readFileSync(new URL('./fixtures/runtime-check/app/index.tsx', import.meta.url), 'utf8');
  await s.setBundle({ 'app/index.tsx': index.replace("BUILD = 'v1'", "BUILD = 'v2'") });
  s.platform.emit({ type: 'app_built', app_id: '00000000-0000-4000-8000-000000000000', version: '9' });
  s.platform.emit({ type: 'app_built', app_id: APP_ID, version: '1.0.1' });
  const loads = await until('bundle.load', () => {
    const l = s.toSandbox.filter((m) => m.kind === 'evt' && m.event === 'bundle.load');
    return l.length && l;
  });
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(loads.length, 1);
  assert.match(loads[0].data.code, /BUILD = "v2"/);
  s.close();
});

test('the runtime provides exactly modules.json, and the typings declare the non-React ones', async () => {
  const dom = new JSDOM('<!doctype html><div id="root"></div>', { runScripts: 'outside-only', pretendToBeVisual: true });
  dom.window.ReactNativeWebView = { postMessage() {} };
  new vm.Script(runtimeCode).runInContext(dom.getInternalVMContext());
  let req;
  dom.window.__homeai_define((require) => void (req = require));
  for (const m of ALLOWED_MODULES) assert.ok(req(m), m);
  for (const m of ['react-dom', 'react-dom/client', 'react-native-web', 'fs']) assert.throws(() => req(m), /not available in the app sandbox/);
  const sdk = req('@homeai/sdk');
  assert.deepEqual(Object.keys(sdk).sort(), ['runAction', 'useDatabase', 'useQuery', 'useSQLiteContext', 'useSpace']);
  assert.equal(sdk.useSQLiteContext, sdk.useDatabase);
  await assert.rejects(sdk.useDatabase().withTransactionAsync(async () => {}), /not available in SDK 1: put multi-statement writes in an action/);
  assert.deepEqual(Object.keys(req('expo-sqlite')).sort(), ['SQLiteProvider', 'openDatabaseAsync', 'useSQLiteContext']);
  const declared = [...fs.readFileSync(SDK_TYPES, 'utf8').matchAll(/declare module '([^']+)'/g)].map((m) => m[1]);
  assert.deepEqual(declared.sort(), ALLOWED_MODULES.filter((m) => !m.startsWith('react')).sort());
  dom.window.close();
});

test('the sandbox ignores anything that is not a v1 envelope, and messages not from its parent', async () => {
  const s = await boot();
  const before = s.text('count');
  for (const junk of ['{', 'null', '{"homeai":2,"kind":"evt","event":"space","data":{}}', JSON.stringify({ homeai: 1, kind: 'evt', event: 'space', data: { name: 'x' } }).padEnd(1 << 21)]) {
    s.win.__homeaiReceive(junk);
  }
  s.win.__homeaiReceive({ homeai: 1, kind: 'evt', event: 'space', data: { name: 'object, not a string' } });
  s.win.dispatchEvent(new s.win.MessageEvent('message', { data: JSON.stringify({ homeai: 1, kind: 'evt', event: 'space', data: { ...SPACE, name: 'spoof' } }) }));
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(s.text('space'), 'Family (editor)');
  assert.equal(s.text('count'), before);
  s.close();
});
