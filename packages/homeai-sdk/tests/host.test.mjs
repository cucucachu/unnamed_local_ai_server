// `@homeai/sdk/host`: what the host lets through from the sandbox, how it
// talks to the platform, and the sandbox document.
import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { APP_ID, INSTANCE_ID, importHost } from './helpers.mjs';

const H = await importHost();
const req = (id, method, params) => JSON.stringify({ homeai: 1, kind: 'req', id, method, params });
const tick = () => new Promise((r) => setTimeout(r, 0));

function harness({ readOnly = false, forward, onAskAgent } = {}) {
  const sent = [];
  const forwarded = [];
  const events = [];
  const host = H.createBridgeHost({
    send: (wire) => sent.push(JSON.parse(wire)),
    forward:
      forward ??
      (async (method, params) => {
        forwarded.push({ method, params });
        return method === 'db.getAll' ? [{ n: 1 }] : { changes: 1, lastInsertRowId: 7 };
      }),
    readOnly,
    onEvent: (event, data) => events.push({ event, data }),
    onAskAgent,
  });
  return { host, sent, forwarded, events, res: (id) => sent.find((m) => m.kind === 'res' && m.id === id) };
}

test('allowed methods are forwarded with only their known params', async () => {
  const h = harness();
  h.host.receive(req(1, 'db.getAll', { sql: 'SELECT 1', params: [1], instance_id: 'other', url: 'http://x' }));
  h.host.receive(req(2, 'action', { name: 'addItem', params: { name: 'x' }, instance: 'other' }));
  h.host.receive(req(3, 'db.run', { sql: 'DELETE FROM items' }));
  await tick();
  assert.deepEqual(h.forwarded, [
    { method: 'db.getAll', params: { sql: 'SELECT 1', params: [1] } },
    { method: 'action', params: { name: 'addItem', params: { name: 'x' } } },
    { method: 'db.run', params: { sql: 'DELETE FROM items', params: [] } },
  ]);
  assert.deepEqual(h.res(1), { homeai: 1, kind: 'res', id: 1, ok: true, result: [{ n: 1 }] });
});

test('other methods, bad params and viewer writes are refused without forwarding', async () => {
  const h = harness({ readOnly: true });
  h.host.receive(req(1, 'transaction', { statements: [] }));
  h.host.receive(req(2, 'db.run', { sql: 'DELETE FROM items' }));
  h.host.receive(req(3, 'action', { name: 'x' }));
  h.host.receive(req(4, 'db.getAll', { sql: 42 }));
  h.host.receive(req(5, 'db.getAll', { sql: 'SELECT 1', params: 'x' }));
  h.host.receive(req(6, 'db.getAll', { sql: 'x'.repeat(100 * 1024 + 1) }));
  h.host.receive(req(7, 'db.getAll', null));
  h.host.receive(req(8, '__proto__', {}));
  await tick();
  assert.deepEqual(h.forwarded, []);
  assert.deepEqual(
    [1, 2, 3, 4, 5, 6, 7, 8].map((id) => h.res(id).error.code),
    ['method_not_allowed', 'read_only', 'read_only', 'bad_request', 'bad_request', 'bad_request', 'bad_request', 'method_not_allowed'],
  );
});

test('agent.ask is handled by the host and never forwarded, including for viewers', async () => {
  const asked = [];
  const h = harness({
    readOnly: true,
    onAskAgent: (prompt) => asked.push(prompt),
  });
  h.host.receive(req(1, 'agent.ask', { prompt: 'Add milk', instance_id: 'other', url: 'http://x' }));
  h.host.receive(req(2, 'agent.ask', { prompt: 1 }));
  h.host.receive(req(3, 'agent.ask', null));
  h.host.receive(req(4, 'agent.ask', { prompt: 'x'.repeat(32 * 1024 + 1) }));
  await tick();
  assert.deepEqual(asked, ['Add milk']);
  assert.deepEqual(h.forwarded, []);
  assert.deepEqual(h.res(1), { homeai: 1, kind: 'res', id: 1, ok: true, result: {} });
  assert.equal(h.res(2).error.code, 'bad_request');
  assert.equal(h.res(3).error.code, 'bad_request');
  assert.equal(h.res(4).error.code, 'bad_request');
});

test('forward errors keep their code; the in-flight cap answers busy', async () => {
  let release;
  const gate = new Promise((r) => (release = r));
  const h = harness({
    forward: async (method, params) => {
      if (params.sql === 'bad') throw Object.assign(new Error('no such table: x'), { code: 'sql_error' });
      if (params.sql === 'boom') throw new Error('boom');
      await gate;
      return [];
    },
  });
  h.host.receive(req(1, 'db.getAll', { sql: 'bad' }));
  h.host.receive(req(2, 'db.getAll', { sql: 'boom' }));
  await tick();
  assert.deepEqual(h.res(1).error, { code: 'sql_error', message: 'no such table: x' });
  assert.deepEqual(h.res(2).error, { code: 'host_error', message: 'boom' });
  for (let i = 0; i < H.MAX_IN_FLIGHT + 1; i++) h.host.receive(req(100 + i, 'db.getAll', { sql: 'wait' }));
  await tick();
  assert.equal(h.res(100 + H.MAX_IN_FLIGHT).error.code, 'busy');
  release();
  await tick();
  assert.equal(h.sent.filter((m) => m.kind === 'res' && m.ok).length, H.MAX_IN_FLIGHT);
});

test('junk is ignored; only sandbox events reach onEvent; host events wait until the sandbox speaks', async () => {
  const h = harness();
  h.host.dbChanged();
  h.host.setSpace({ id: 's', slug: 's', name: 'S', role: 'viewer' });
  assert.deepEqual(h.sent, []);
  for (const junk of [{}, '{', 'null', '{"homeai":2,"kind":"req","id":1,"method":"db.getAll"}', '{"homeai":1,"kind":"req","id":"1","method":"db.getAll"}', ' '.repeat(H.MAX_MESSAGE_CHARS + 1)]) {
    h.host.receive(junk);
  }
  assert.deepEqual(h.sent, []);
  h.host.receive(JSON.stringify({ homeai: 1, kind: 'evt', event: 'runtime.ready', data: { version: 1, renderMs: 2 } }));
  h.host.receive(JSON.stringify({ homeai: 1, kind: 'evt', event: 'db.changed', data: {} }));
  h.host.receive(JSON.stringify({ homeai: 1, kind: 'res', id: 1, ok: true, result: 1 }));
  assert.deepEqual(h.events, [{ event: 'runtime.ready', data: { version: 1, renderMs: 2 } }]);
  assert.deepEqual(
    h.sent.map((m) => m.event),
    ['db.changed', 'space'],
  );
  h.host.close();
  h.host.receive(req(9, 'db.getAll', { sql: 'SELECT 1' }));
  h.host.dbChanged();
  await tick();
  assert.equal(h.sent.length, 2);
});

function fakeFetch(responses) {
  const calls = [];
  const fetch = async (url, init = {}) => {
    calls.push({ url, ...init, body: init.body && JSON.parse(init.body) });
    const r = responses.shift();
    if (r instanceof Error) throw r;
    return { ok: r.status < 300, status: r.status, json: async () => (r.body === undefined ? Promise.reject(new SyntaxError('empty')) : r.body), text: async () => String(r.body) };
  };
  return { fetch, calls };
}

test('platformForward maps each method onto the instance RPC and back', async () => {
  const f = fakeFetch([
    { status: 200, body: { rows: [{ a: 1 }] } },
    { status: 200, body: { row: null } },
    { status: 200, body: { changes: 2, lastInsertRowId: 5 } },
    { status: 200, body: { changes: 1, lastInsertRowId: 3, rows: [{ n: 1 }] } },
  ]);
  const forward = H.platformForward(INSTANCE_ID, { baseUrl: 'http://homeai.local', fetch: f.fetch, headers: { Authorization: 'Bearer t' } });
  assert.deepEqual(await forward('db.getAll', { sql: 'SELECT a', params: [] }), [{ a: 1 }]);
  assert.equal(await forward('db.getFirst', { sql: 'SELECT a', params: { $a: 1 } }), null);
  assert.deepEqual(await forward('db.run', { sql: 'UPDATE t SET a = 1', params: [] }), { changes: 2, lastInsertRowId: 5 });
  assert.deepEqual(await forward('action', { name: 'go', params: { x: 1 } }), { changes: 1, lastInsertRowId: 3, rows: [{ n: 1 }] });
  const url = `http://homeai.local/api/platform/apps/instances/${INSTANCE_ID}/rpc`;
  assert.ok(f.calls.every((c) => c.url === url && c.method === 'POST' && c.headers.Authorization === 'Bearer t' && c.headers['Content-Type'] === 'application/json'));
  assert.deepEqual(
    f.calls.map((c) => c.body),
    [
      { op: 'getAll', sql: 'SELECT a', params: [] },
      { op: 'getFirst', sql: 'SELECT a', params: { $a: 1 } },
      { op: 'run', sql: 'UPDATE t SET a = 1', params: [] },
      { op: 'action', name: 'go', params: { x: 1 } },
    ],
  );
});

test('platform errors become {code, message}', async () => {
  const f = fakeFetch([
    { status: 422, body: { detail: 'sql_error', message: 'no such table: x' } },
    { status: 404, body: { detail: 'not_found' } },
    { status: 502 },
    new TypeError('Failed to fetch'),
  ]);
  const forward = H.platformForward(INSTANCE_ID, { fetch: f.fetch });
  const codeOf = (p) => p.then(() => null, (e) => ({ code: e.code, message: e.message }));
  assert.deepEqual(await codeOf(forward('db.getAll', { sql: 'x', params: [] })), { code: 'sql_error', message: 'no such table: x' });
  assert.deepEqual(await codeOf(forward('db.getAll', { sql: 'x', params: [] })), { code: 'not_found', message: 'not_found' });
  assert.deepEqual(await codeOf(forward('db.getAll', { sql: 'x', params: [] })), { code: 'http_502', message: 'http_502' });
  assert.equal((await codeOf(forward('db.getAll', { sql: 'x', params: [] }))).code, 'unavailable');
});

test('bundle and runtime URLs', async () => {
  const f = fakeFetch([{ status: 200, body: { code: 'x' } }]);
  await H.fetchBundle(INSTANCE_ID, { fetch: f.fetch });
  assert.equal(f.calls[0].url, `/api/platform/apps/instances/${INSTANCE_ID}/bundle`);
  assert.equal(f.calls[0].method, 'GET');
  assert.equal(H.runtimeUrl('1'), '/app-runtime/1/runtime.js');
  assert.equal(H.runtimeUrl('1', 'http://homeai.local'), 'http://homeai.local/app-runtime/1/runtime.js');
});

test('the event relay passes on this instance and this app only, coalescing reloads', async () => {
  const seen = [];
  const host = { dbChanged: () => seen.push('db.changed'), loadBundle: (code) => seen.push(`load ${code}`) };
  let n = 0;
  let release;
  const relay = H.platformEventRelay({
    instanceId: INSTANCE_ID,
    appId: APP_ID,
    host,
    reload: () => new Promise((r) => (release = () => r(`v${++n}`))),
  });
  relay('{"type":"ready"}');
  relay(JSON.stringify({ type: 'db_changed', instance_id: INSTANCE_ID }));
  relay(JSON.stringify({ type: 'db_changed', instance_id: 'someone-else' }));
  relay(JSON.stringify({ type: 'app_built', app_id: 'other-app', version: '2' }));
  relay('not json');
  assert.deepEqual(seen, ['db.changed', 'db.changed']);
  relay({ type: 'app_built', app_id: APP_ID, version: '2' });
  relay({ type: 'app_built', app_id: APP_ID, version: '3' });
  relay({ type: 'app_built', app_id: APP_ID, version: '4' });
  release();
  await tick();
  await tick();
  release();
  await tick();
  await tick();
  assert.deepEqual(seen.slice(2), ['load v1', 'load v2']);
});

test('the sandbox document: CSP first, scripts that cannot close their element, escaped config', () => {
  const html = H.sandboxDocument({ runtime: 'var r = "</script><b>";', app: 'var a = "</SCRIPT>";', config: { initialPath: '/', space: { id: 'i', slug: 's', name: '</script><img src=x>', role: 'owner' } } });
  const head = html.slice(0, html.indexOf('<script>'));
  assert.ok(head.includes(`<meta http-equiv="Content-Security-Policy" content="${H.SANDBOX_CSP}">`));
  assert.equal(H.SANDBOX_CSP, "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'");
  assert.equal((html.match(/<\/script>/gi) ?? []).length, 3);
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
  const ctx = vm.createContext({ window: {} });
  for (const s of scripts) vm.runInContext(s, ctx);
  assert.equal(ctx.window.__homeai_config.space.name, '</script><img src=x>');
  assert.equal(vm.runInContext('r + a', ctx), '</script><b></SCRIPT>');
});

test('injectScriptFor delivers the exact wire string', () => {
  const wire = JSON.stringify({ homeai: 1, kind: 'evt', event: 'space', data: { name: `a"b'c\u2028</script>\\` } });
  const got = [];
  const ctx = vm.createContext({ window: { __homeaiReceive: (s) => got.push(s) } });
  assert.equal(vm.runInContext(H.injectScriptFor(wire), ctx), true);
  assert.deepEqual(got, [wire]);
});
