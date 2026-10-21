// The runtime (dev build) and the fixture app in jsdom, over the WebView
// transport (`ReactNativeWebView.postMessage` / `__homeaiReceive`), with the
// real host library in front of a stand-in platform (helpers.mjs).
//   npm test    (builds dist/ first)
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { JSDOM } from 'jsdom';
import { ALLOWED_MODULES, OPTIONAL_MODULES, SDK_ROOT, SDK_TYPES, optionalModulesOf } from '../build.mjs';
import { APP_ID, FIXTURE, INSTANCE_ID, RUNTIME_DEV, bundleApp, importHost, stubPlatform } from './helpers.mjs';

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
async function boot({ config = { initialPath: '/', space: SPACE }, readOnly = false, edits, images } = {}) {
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
  const asked = [];
  const events = [];
  const wire = [];
  const toSandbox = [];
  let bundleCode = await bundleApp(undefined, { edits });
  const bridge = host.createBridgeHost({
    send: (s) => (toSandbox.push(JSON.parse(s)), win.__homeaiReceive(s)),
    forward: host.platformForward(INSTANCE_ID, { fetch: platform.fetch(async () => bundleCode) }),
    readOnly,
    onEvent: (event, data) => events.push({ event, data }),
    onAskAgent: (prompt) => asked.push(prompt),
    images,
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
    asked,
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

test('askAgent is a host-local RPC; a write elsewhere still updates the UI', async () => {
  const s = await boot();
  s.click('ask-agent');
  await until('askAgent', () => s.asked[0] === 'Add Milk via app_sql');
  assert.equal(s.requests('agent.ask').length, 1);
  assert.deepEqual(s.requests('agent.ask')[0].params, { prompt: 'Add Milk via app_sql' });
  assert.equal(s.platform.calls.length, s.requests('db.getAll').length);
  s.platform.externalWrite("INSERT INTO items (name) VALUES ('Milk')");
  await until('agent write shown', () => s.text('item-1') === 'Milk' && s.text('count') === '1 items');
  s.close();
});

test('PhotoField picks through the host and Photo shows it from one cached image.get', async () => {
  const index = fs.readFileSync(new URL('./fixtures/runtime-check/app/index.tsx', import.meta.url), 'utf8');
  const edited = index
    .replace("import { Link } from 'expo-router';", "import { Link } from 'expo-router';\nimport { Photo, PhotoField } from '@homeai/ui';")
    .replace("  const [status, setStatus] = useState('');", "  const [status, setStatus] = useState('');\n  const [photo, setPhoto] = useState<string | null>(null);")
    .replace(
      '<Text testID="status">',
      `<PhotoField testID="pf" label="Photo" value={photo} onChange={setPhoto} />
      <Photo testID="again" id={photo} />
      <Text testID="photo-id">{photo ?? 'none'}</Text>
      <Text testID="status">`,
    );
  const ID = 'b'.repeat(32) + '.jpg';
  const URI = 'data:image/jpeg;base64,/9j/AAAA';
  const calls = [];
  const images = {
    pick: async (source) => (calls.push(['pick', source]), { id: ID, width: 10, height: 10 }),
    get: async (id) => (calls.push(['get', id]), URI),
    remove: async () => {},
  };
  const s = await boot({ edits: { 'app/index.tsx': edited }, images });
  assert.ok(s.text('pf-library').endsWith('Choose photo'));
  assert.equal(s.$('pf-remove'), null);
  s.click('pf-library');
  await until('picked', () => s.text('photo-id') === ID);
  const shown = (id) => s.$(id)?.querySelector('img')?.getAttribute('src');
  await until('shown twice', () => shown('pf-preview') === URI && shown('again') === URI);
  assert.deepEqual(calls, [['pick', 'library'], ['get', ID]]);
  assert.ok(s.text('pf-library').endsWith('Choose another'));
  s.click('pf-remove');
  await until('removed', () => s.text('photo-id') === 'none' && !shown('pf-preview'));
  s.close();
});

test('search() reaches the instance RPC, also read-only; a blank query makes no call', async () => {
  const index = fs.readFileSync(new URL('./fixtures/runtime-check/app/index.tsx', import.meta.url), 'utf8');
  const edited = index
    .replace('askAgent, runAction,', 'askAgent, runAction, search,')
    .replace(
      '<Text testID="status">',
      `<Pressable testID="search" onPress={() => void search(name, 99).then((r) => setStatus(r.map((h) => h.table + ' #' + h.id + ' ' + h.title).join(', ') || 'none'))}>
        <Text>Search</Text>
      </Pressable>
      <Text testID="status">`,
    );
  const s = await boot({ readOnly: true, edits: { 'app/index.tsx': edited } });
  s.platform.externalWrite("INSERT INTO items (name) VALUES ('Lentil soup'), ('Bread'), ('Lentil salad')");
  await until('rows', () => s.text('count') === '3 items');
  s.type('new-item', 'Lentil');
  s.click('search');
  await until('results', () => s.text('status') === 'items #1 Lentil soup, items #3 Lentil salad');
  assert.deepEqual(s.requests('search')[0].params, { query: 'Lentil', limit: 50 });
  assert.deepEqual(s.platform.calls.at(-1), { op: 'search', query: 'Lentil', limit: 50 });
  s.type('new-item', '  ');
  s.click('search');
  await until('none', () => s.text('status') === 'none');
  assert.equal(s.requests('search').length, 1);
  s.close();
});

test('runExportAction sends another app’s action through the instance RPC, also read-only', async () => {
  const index = fs.readFileSync(new URL('./fixtures/runtime-check/app/index.tsx', import.meta.url), 'utf8');
  const edited = index
    .replace('askAgent, runAction,', 'askAgent, runAction, runExportAction,')
    .replace(
      '<Text testID="status">',
      `<Pressable testID="to-list" onPress={() => void runExportAction('i-grocery', 'items', 'addItem', { name }).then((r) => setStatus('added #' + r.lastInsertRowId), (e) => setStatus(e.code))}>
        <Text>To list</Text>
      </Pressable>
      <Pressable testID="to-nowhere" onPress={() => void runExportAction('i-grocery', 'nope', 'addItem').then(() => setStatus('?'), (e) => setStatus(e.code))}>
        <Text>Nowhere</Text>
      </Pressable>
      <Text testID="status">`,
    );
  const s = await boot({ readOnly: true, edits: { 'app/index.tsx': edited } });
  s.type('new-item', 'Flour');
  s.click('to-list');
  await until('added', () => s.text('status') === 'added #41');
  assert.deepEqual(s.platform.calls.at(-1), { op: 'exportAction', instance: 'i-grocery', export: 'items', name: 'addItem', params: { name: 'Flour' } });
  s.click('to-nowhere');
  await until('refused', () => s.text('status') === 'unknown_export');
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

test('useUser / useMembers / useMember come from the host config, then its events', async () => {
  const alice = { id: 'u-alice', username: 'alice', name: 'Alice', role: 'owner' };
  const s = await boot({ config: { initialPath: '/', space: SPACE, user: alice, members: [alice] } });
  assert.equal(s.text('user'), 'Alice @alice');
  assert.equal(s.text('members'), '1 members, alice is Alice');
  s.bridge.event('members', [{ ...alice, name: 'Al' }, { id: 'u-bob', username: 'bob', name: 'Bob', role: 'editor' }]);
  await until('members', () => s.text('members') === '2 members, alice is Al');
  s.bridge.event('user', { id: 'u-bob', username: 'bob', name: 'Bob' });
  await until('user', () => s.text('user') === 'Bob @bob');
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
  for (const m of ALLOWED_MODULES.filter((m) => !OPTIONAL_MODULES.includes(m))) assert.ok(req(m), m);
  for (const m of OPTIONAL_MODULES) assert.throws(() => req(m), /didn't load; reopen the app/);
  for (const m of ['react-dom', 'react-dom/client', 'react-native-web', 'fs']) assert.throws(() => req(m), /not available in the app sandbox/);
  for (const m of OPTIONAL_MODULES) {
    new vm.Script(fs.readFileSync(path.join(SDK_ROOT, 'dist', `${m}.js`), 'utf8')).runInContext(dom.getInternalVMContext());
    assert.ok(req(m), m);
  }
  assert.equal(typeof req('three').WebGLRenderer, 'function');
  dom.window.__homeai_provide('fs', {});
  assert.throws(() => req('fs'), /not available in the app sandbox/);
  assert.deepEqual(Object.keys(req('expo-gl')), ['GLView']);
  const sdk = req('@homeai/sdk');
  assert.deepEqual(Object.keys(sdk).sort(), ['askAgent', 'deleteImage', 'imageUri', 'pickImage', 'runAction', 'runExportAction', 'search', 'useDatabase', 'useImage', 'useMember', 'useMembers', 'useQuery', 'useSQLiteContext', 'useSpace', 'useUser']);
  assert.equal(sdk.useSQLiteContext, sdk.useDatabase);
  await assert.rejects(sdk.useDatabase().withTransactionAsync(async () => {}), /not available in SDK 1: put multi-statement writes in an action/);
  assert.deepEqual(Object.keys(req('expo-sqlite')).sort(), ['SQLiteProvider', 'openDatabaseAsync', 'useSQLiteContext']);
  const declared = [...fs.readFileSync(SDK_TYPES, 'utf8').matchAll(/declare module '([^']+)'/g)].map((m) => m[1]);
  // three's typings are @types/three's.
  assert.deepEqual(declared.sort(), ALLOWED_MODULES.filter((m) => !m.startsWith('react') && m !== 'three').sort());
  dom.window.close();
});

test('optional modules: one list in build.mjs and protocol.ts, found by the require() esbuild leaves', async () => {
  const H = await importHost();
  assert.deepEqual([...H.OPTIONAL_MODULES], OPTIONAL_MODULES);
  for (const f of [optionalModulesOf, H.optionalModulesOf]) {
    assert.deepEqual(f('var t=__toESM(require("three"));'), ['three']);
    assert.deepEqual(f("require( 'three' )"), ['three']);
    assert.deepEqual(f('require("three-stdlib");x.require("threex")'), []);
    assert.deepEqual(f('require("react-native")'), []);
  }
  assert.ok(optionalModulesOf(await bundleApp(path.join(path.dirname(FIXTURE), 'gl-check'), { dev: false })).includes('three'));
  assert.deepEqual(optionalModulesOf(await bundleApp(FIXTURE, { dev: false })), []);
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
