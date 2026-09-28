import { PROTOCOL } from '@homeai/sdk/host';

import { instanceBridge, isReadOnly, loadInstanceDocument, sandboxSpace } from '../appHost';
import type { Space } from '../platform';
import { onUnauthorized, setSessionToken } from '../session';

// jest-expo runs as iOS, so requests go to `apiBase()`'s native default.
const BASE = 'http://homeai.local';
const FIXED = '11111111-1111-4111-8111-111111111111';
const OTHER = '22222222-2222-4222-8222-222222222222';
const TOKEN = 'hs_secret-bearer';

type Call = { url: string; init: { method?: string; headers?: Record<string, string>; body?: string } };

function mockPlatform(reply: (call: Call) => { status?: number; body?: unknown; text?: string } = () => ({ body: { rows: [], row: null, changes: 1, lastInsertRowId: 7 } })) {
  const calls: Call[] = [];
  global.fetch = jest.fn(async (url: string, init: Call['init'] = {}) => {
    const call = { url: String(url), init };
    calls.push(call);
    const { status = 200, body, text } = reply(call);
    return {
      ok: status < 300,
      status,
      json: async () => body,
      text: async () => text ?? JSON.stringify(body),
    };
  }) as unknown as typeof fetch;
  return calls;
}

const req = (id: number, method: string, params?: unknown) => JSON.stringify({ homeai: PROTOCOL, kind: 'req', id, method, params });

/** A bridge for FIXED plus every wire it sent back to the sandbox. */
function openBridge(readOnly = false) {
  const sent: string[] = [];
  const host = instanceBridge(FIXED, { send: (wire) => sent.push(wire), readOnly });
  const replies = () => sent.map((wire) => JSON.parse(wire)).filter((env) => env.kind === 'res');
  const answered = async (n: number) => {
    for (let i = 0; i < 50 && replies().length < n; i++) await new Promise((resolve) => setTimeout(resolve, 0));
    return replies();
  };
  return { host, sent, answered };
}

beforeEach(() => setSessionToken(TOKEN));
afterEach(() => setSessionToken(null));

describe('instanceBridge: forwarding is bound to the instance the host opened', () => {
  it('sends every method to that instance’s RPC endpoint, whatever the message names', async () => {
    const calls = mockPlatform();
    const { host, answered } = openBridge();
    const redirect = {
      instance_id: OTHER,
      instanceId: OTHER,
      id: OTHER,
      url: `${BASE}/api/platform/apps/instances/${OTHER}/rpc`,
      path: `/api/platform/apps/instances/${OTHER}/rpc`,
      endpoint: '/api/platform/files/content',
      baseUrl: 'https://attacker.example',
      op: 'transaction',
      headers: { Authorization: 'Bearer hs_other' },
    };
    host.receive(req(1, 'db.getAll', { sql: 'SELECT 1', params: [], ...redirect }));
    host.receive(req(2, 'db.getFirst', { sql: 'SELECT 1', ...redirect }));
    host.receive(req(3, 'db.run', { sql: 'INSERT INTO t VALUES (1)', params: { $a: 1 }, ...redirect }));
    host.receive(req(4, 'action', { name: `../../${OTHER}/rpc`, params: { x: 1 }, ...redirect }));
    await answered(4);

    expect(calls.map((c) => c.url)).toEqual(Array(4).fill(`${BASE}/api/platform/apps/instances/${FIXED}/rpc`));
    expect(calls.map((c) => JSON.parse(c.init.body!))).toEqual([
      { op: 'getAll', sql: 'SELECT 1', params: [] },
      { op: 'getFirst', sql: 'SELECT 1', params: [] },
      { op: 'run', sql: 'INSERT INTO t VALUES (1)', params: { $a: 1 } },
      { op: 'action', name: `../../${OTHER}/rpc`, params: { x: 1 } },
    ]);
    for (const { init } of calls) {
      expect(init.method).toBe('POST');
      expect(init.headers?.Authorization).toBe(`Bearer ${TOKEN}`);
    }
  });

  it('refuses methods outside the allowlist and malformed envelopes without any request', async () => {
    const calls = mockPlatform();
    const { host, sent, answered } = openBridge();
    const names = ['fetch', 'bundle', 'rpc', 'db.transaction', `instances/${OTHER}/rpc`, '../files/content', 'constructor', '__proto__'];
    names.forEach((method, i) => host.receive(req(i + 1, method, { sql: 'SELECT 1' })));
    const replies = await answered(names.length);
    expect(replies.map((r) => r.error.code)).toEqual(Array(names.length).fill('method_not_allowed'));

    const before = sent.length;
    host.receive({ homeai: PROTOCOL, kind: 'req', id: 99, method: 'db.getAll', params: { sql: 'SELECT 1' } });
    host.receive(JSON.stringify({ homeai: 2, kind: 'req', id: 100, method: 'db.getAll', params: { sql: 'SELECT 1' } }));
    host.receive('not json');
    host.receive(req(101, 'db.getAll', 'SELECT 1'));
    const [bad] = (await answered(names.length + 1)).slice(names.length);
    expect(bad).toMatchObject({ id: 101, ok: false, error: { code: 'bad_request' } });
    expect(sent.length).toBe(before + 1);
    expect(calls).toEqual([]);
  });

  it('handles agent.ask locally and never names an instance on the platform', async () => {
    const calls = mockPlatform();
    const asked: string[] = [];
    const sent: string[] = [];
    const host = instanceBridge(FIXED, { send: (wire) => sent.push(wire), onAskAgent: (prompt) => asked.push(prompt) });
    host.receive(req(1, 'agent.ask', { prompt: 'Add milk', instance_id: OTHER, url: `${BASE}/api/platform/apps/instances/${OTHER}/rpc` }));
    for (let i = 0; i < 50 && sent.length < 1; i++) await new Promise((resolve) => setTimeout(resolve, 0));
    expect(asked).toEqual(['Add milk']);
    expect(JSON.parse(sent[0])).toMatchObject({ kind: 'res', id: 1, ok: true, result: {} });
    expect(calls).toEqual([]);
  });

  it('never hands the sandbox the host’s credentials', async () => {
    mockPlatform(() => ({ status: 403, body: { detail: 'insufficient_role' } }));
    const { host, sent, answered } = openBridge();
    host.receive(req(1, 'db.run', { sql: 'DELETE FROM t' }));
    const [reply] = await answered(1);
    expect(reply).toMatchObject({ ok: false, error: { code: 'insufficient_role' } });
    expect(sent.join('\n')).not.toContain(TOKEN);
  });

  it('signs out when the platform says the session is gone', async () => {
    mockPlatform(() => ({ status: 401, body: { detail: 'unauthenticated' } }));
    const listener = jest.fn();
    const unsubscribe = onUnauthorized(listener);
    const { host, answered } = openBridge();
    host.receive(req(1, 'db.getAll', { sql: 'SELECT 1' }));
    await answered(1);
    unsubscribe();
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe('instanceBridge: viewers are read-only', () => {
  it('refuses db.run and action before they reach the platform, and still reads', async () => {
    const calls = mockPlatform(() => ({ body: { rows: [{ id: 1 }] } }));
    const { host, answered } = openBridge(true);
    host.receive(req(1, 'db.run', { sql: 'INSERT INTO t VALUES (1)' }));
    host.receive(req(2, 'action', { name: 'markAllDone' }));
    host.receive(req(3, 'db.getAll', { sql: 'SELECT id FROM t' }));
    const replies = await answered(3);
    const byId = Object.fromEntries(replies.map((r) => [r.id, r]));
    expect(byId[1]).toMatchObject({ ok: false, error: { code: 'read_only' } });
    expect(byId[2]).toMatchObject({ ok: false, error: { code: 'read_only' } });
    expect(byId[3]).toMatchObject({ ok: true, result: [{ id: 1 }] });
    expect(calls.map((c) => JSON.parse(c.init.body!).op)).toEqual(['getAll']);
  });

  it('is read-only for viewers and for a missing role', () => {
    const space = (role: Space['role']): Space => ({
      id: 's1',
      slug: 'home',
      name: 'Home',
      kind: 'shared',
      gid: 3000,
      owner_user_id: null,
      role,
      created_at: '',
      archived_at: null,
    });
    expect([isReadOnly(space('owner')), isReadOnly(space('editor')), isReadOnly(space('viewer')), isReadOnly(space(null))]).toEqual([
      false,
      false,
      true,
      true,
    ]);
    expect(sandboxSpace(space(null))).toEqual({ id: 's1', slug: 'home', name: 'Home', role: 'viewer' });
  });
});

describe('loadInstanceDocument', () => {
  it('builds the CSP’d sandbox document from the instance bundle and its SDK runtime', async () => {
    const calls = mockPlatform(({ url }) =>
      url.endsWith('/bundle')
        ? { body: { app_id: 'app-1', version: '1.0.0', sdk: '1', bundle_id: 'b1', code: '__homeai_define(function(){})' } }
        : { text: 'window.__runtime = 1;' },
    );
    const space: Space = {
      id: 's1',
      slug: 'personal-alice',
      name: 'Alice',
      kind: 'personal',
      gid: 3000,
      owner_user_id: 'u1',
      role: 'owner',
      created_at: '',
      archived_at: null,
    };
    const doc = await loadInstanceDocument(FIXED, space);
    expect(calls.map((c) => c.url)).toEqual([`${BASE}/api/platform/apps/instances/${FIXED}/bundle`, `${BASE}/app-runtime/1/runtime.js`]);
    expect(doc).toMatchObject({ appId: 'app-1', version: '1.0.0' });
    expect(doc.html).toContain("connect-src 'none'");
    expect(doc.html).toContain('window.__runtime = 1;');
    expect(doc.html).toContain('__homeai_define(function(){})');
    expect(doc.html).toContain('"role":"owner"');
    expect(doc.html).not.toContain(FIXED);
    expect(doc.html).not.toContain(TOKEN);
  });
});
