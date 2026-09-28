// Runtime harness: a static test host page (the host library, as M12-06's
// web host will use it) that opens one app instance in the sandboxed iframe,
// plus the checks run against it. Used offline against a stand-in platform
// (tests/runtime.browser.test.mjs) and live against the real one
// (scripts/e2e/app_runtime_smoke.mjs); the page and the checks are the same.
//
// The page is served with `page.route` at `<origin>/__homeai_runtime_harness__`,
// so on the live stack it has the origin, session cookie and routes a web host
// has. Everything it does goes through the host library: fetchBundle,
// fetchRuntime, sandboxDocument, mountSandboxFrame, platformForward, and
// platformEventRelay over `/ws/platform/events`.
import fs from 'node:fs';
import { hostIife } from '../helpers.mjs';

export const HOST_PATH = '/__homeai_runtime_harness__';
export const PROBE_PATH = '/__homeai_probe__';

const HOST_SCRIPT = `
(async () => {
  const H = window.HomeaiHost;
  const cfg = JSON.parse(document.getElementById('cfg').textContent);
  const h = (window.harness = { events: [], frames: [], killed: false, error: null, H });
  try {
    const bundle = await H.fetchBundle(cfg.instanceId);
    const runtime = await H.fetchRuntime(bundle.sdk);
    const html = H.sandboxDocument({ runtime, app: bundle.code, config: { initialPath: '/', space: cfg.space } });
    h.srcdocBytes = html.length;
    const sb = H.mountSandboxFrame(document.getElementById('app'), {
      html,
      forward: H.platformForward(cfg.instanceId),
      readOnly: cfg.readOnly,
      onEvent: (event, data) => h.events.push({ event, data }),
      onKilled: () => (h.killed = true),
    });
    h.sandbox = sb;
    const relay = H.platformEventRelay({
      instanceId: cfg.instanceId,
      appId: bundle.app_id,
      host: sb.host,
      reload: async () => (await H.fetchBundle(cfg.instanceId)).code,
      onError: (e) => (h.error = String(e)),
    });
    h.deliver = (wire) => {
      h.frames.push(JSON.parse(wire));
      relay(wire);
    };
    if (cfg.events === 'ws') {
      const ws = new WebSocket(location.origin.replace(/^http/, 'ws') + '/ws/platform/events');
      ws.onmessage = (e) => h.deliver(e.data);
      ws.onclose = (e) => (h.wsClosed = e.code);
    }
  } catch (e) {
    h.error = String(e && e.stack || e);
  }
})();
`;

/** `events`: 'ws' (the platform's /ws/platform/events) or 'push' (frames handed to `harness.deliver`). */
export async function hostPage({ instanceId, space, readOnly = false, events = 'ws' }) {
  const cfg = JSON.stringify({ instanceId, space, readOnly, events }).replace(/</g, '\\u003c');
  return `<!doctype html>
<html><head><meta charset="utf-8"><title>runtime harness</title></head>
<body style="margin:0"><div id="app" style="display:flex;height:640px;width:420px"></div>
<script type="application/json" id="cfg">${cfg}</script>
<script>${(await hostIife()).replace(/<\/script/gi, '<\\/script')}</script>
<script>${HOST_SCRIPT}</script>
</body></html>`;
}

/**
 * Opens the host page. `probes` collects every request to PROBE_PATH (from
 * any frame), `unexpected` anything the sandbox frame requested at all.
 */
export async function openHarness(page, { origin, instanceId, space, readOnly = false, events = 'ws', onReady }) {
  const probes = [];
  const fromSandbox = [];
  const html = await hostPage({ instanceId, space, readOnly, events });
  await page.route(`${origin}${HOST_PATH}`, (route) => route.fulfill({ status: 200, contentType: 'text/html', body: html }));
  await page.route(`${origin}${PROBE_PATH}**`, (route) => {
    probes.push(route.request().url());
    return route.fulfill({ status: 200, contentType: 'text/html', body: 'probe' });
  });
  // Chromium reports a request the CSP blocks as issued and then failed;
  // only one that finished (got a response) reached anything.
  const blocked = [];
  page.on('requestfinished', (r) => {
    if (r.frame() !== page.mainFrame()) fromSandbox.push(r.url());
  });
  page.on('requestfailed', (r) => {
    if (r.frame() !== page.mainFrame()) blocked.push(`${new URL(r.url()).search} ${r.failure()?.errorText}`);
  });
  await page.goto(`${origin}${HOST_PATH}`);
  await page.waitForFunction(() => window.harness?.error || window.harness?.events.some((e) => e.event === 'runtime.ready'), null, { timeout: 20000 });
  const error = await page.evaluate(() => window.harness.error);
  if (error) throw new Error(`host page: ${error}`);
  await onReady?.();
  await page.waitForFunction(() => window.harness.frames.some((f) => f.type === 'ready'), null, { timeout: 10000 });
  const frame = page.frames().find((f) => f.parentFrame() === page.mainFrame());
  return { page, frame, ui: page.frameLocator('iframe'), probes, fromSandbox, blocked };
}

// Runs inside the sandbox with app code's privileges. Each entry says whether
// the escape worked.
async function sandboxProbes(target) {
  const out = {};
  const t = async (name, fn) => {
    try {
      const v = await Promise.race([fn(), new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 1500))]);
      out[name] = { escaped: true, value: String(v).slice(0, 120) };
    } catch (e) {
      out[name] = { escaped: false, error: `${e?.name}: ${e?.message}`.slice(0, 160) };
    }
  };
  const origin = new URL(target).origin;
  await t('document.cookie', () => document.cookie || Promise.reject(new Error('empty')));
  await t('localStorage', () => localStorage.getItem('x'));
  await t('sessionStorage', () => sessionStorage.getItem('x'));
  await t('indexedDB', () => new Promise((res, rej) => { const q = indexedDB.open('x'); q.onsuccess = () => res('opened'); q.onerror = () => rej(q.error); }));
  await t('parent.document', () => parent.document.title);
  await t('parent.harness', () => typeof parent.harness.sandbox);
  // Chromium refuses this with a console error, not an exception; runChecks checks the host URL.
  await t('top.location', async () => { top.location.href = `${target}?top`; await new Promise((r) => setTimeout(r, 300)); throw new Error('assigned; the host URL is checked'); });
  await t('fetch platform API with credentials', async () => { const r = await fetch(`${origin}/api/platform/spaces`, { credentials: 'include' }); return `${r.status}`; });
  await t('fetch probe', async () => { const r = await fetch(`${target}?fetch`); return `${r.status}`; });
  await t('fetch no-cors', async () => { const r = await fetch(`${target}?nocors`, { mode: 'no-cors' }); return r.type; });
  await t('XMLHttpRequest', () => new Promise((res, rej) => { const x = new XMLHttpRequest(); x.open('GET', `${target}?xhr`); x.onload = () => res(x.status); x.onerror = () => rej(new Error('xhr error')); x.send(); }));
  await t('WebSocket', () => new Promise((res, rej) => { const ws = new WebSocket(`${origin.replace(/^http/, 'ws')}/ws/platform/events`); ws.onopen = () => res('open'); ws.onerror = () => rej(new Error('ws error')); }));
  await t('EventSource', () => new Promise((res, rej) => { const es = new EventSource(`${target}?sse`); es.onopen = () => res('open'); es.onerror = () => rej(new Error('sse error')); }));
  await t('image beacon', () => new Promise((res, rej) => { const i = new Image(); i.onload = () => res('loaded'); i.onerror = () => rej(new Error('img error')); i.src = `${target}?img`; }));
  await t('sendBeacon', () => (navigator.sendBeacon(`${target}?beacon`, 'x') ? Promise.reject(new Error('queued, see requests')) : Promise.reject(new Error('refused'))));
  await t('script src', () => new Promise((res, rej) => { const s = document.createElement('script'); s.src = `${target}?script`; s.onload = () => res('loaded'); s.onerror = () => rej(new Error('script error')); document.head.appendChild(s); }));
  await t('nested iframe', () => new Promise((res, rej) => { const f = document.createElement('iframe'); f.src = `${target}?iframe`; f.onload = () => (f.contentWindow?.location.href.startsWith('http') ? res('loaded') : rej(new Error('blocked'))); document.body.appendChild(f); setTimeout(() => rej(new Error('blocked')), 1000); }));
  await t('window.open', () => (window.open(`${target}?popup`) ? 'opened' : Promise.reject(new Error('null'))));
  await t('form submit', () => { const f = document.createElement('form'); f.action = `${target}?form`; f.method = 'POST'; f.target = '_blank'; document.body.appendChild(f); f.submit(); return Promise.reject(new Error('submitted, see requests')); });
  return out;
}

// Raw envelopes from inside the sandbox, bypassing the SDK: what the host
// does with methods and params the SDK would never send.
async function rawBridge(requests) {
  const answers = {};
  const want = new Set(requests.map((r) => r.id));
  await new Promise((resolve) => {
    const listener = (e) => {
      if (e.source !== parent) return;
      const env = JSON.parse(e.data);
      if (env.kind === 'res' && want.has(env.id)) {
        answers[env.id] = env;
        want.delete(env.id);
        if (!want.size) resolve();
      }
    };
    addEventListener('message', listener);
    for (const r of requests) parent.postMessage(JSON.stringify({ homeai: 1, kind: 'req', ...r }), '*');
    setTimeout(resolve, 5000);
  });
  return answers;
}

function assert(cond, what, detail) {
  if (!cond) throw new Error(`FAIL: ${what}${detail === undefined ? '' : `: ${JSON.stringify(detail)}`}`);
  console.log(`ok   ${what}`);
}

const events = (page, name) => page.evaluate((n) => window.harness.events.filter((e) => e.event === n).map((e) => e.data), name);

/**
 * The checks. `platform` is the backend adapter:
 *   externalWrite(sql, params)  a write from outside the sandbox (same user, another client)
 *   rows(sql)                   read the instance's database directly
 *   rebuild(edits)              rebuild the app with {file: content} and wait until it's built
 *   otherInstanceId             an instance the sandbox must not reach (or null)
 */
export async function runChecks(h, platform, { SANDBOX_CSP, fixtureIndex }) {
  const { page, frame, ui } = h;
  const visible = (id) => ui.getByTestId(id).filter({ visible: true });
  const text = async (id) => (await visible(id).textContent({ timeout: 10000 }))?.trim();
  const waitText = (id, want) => visible(id).filter({ hasText: want }).waitFor({ timeout: 10000 });

  // The sandbox itself.
  const iframe = page.locator('iframe');
  assert((await iframe.getAttribute('sandbox')) === 'allow-scripts', 'iframe sandbox is exactly "allow-scripts"');
  assert((await iframe.getAttribute('srcdoc'))?.includes(`content="${SANDBOX_CSP}"`), 'srcdoc carries the CSP meta');
  const inside = await frame.evaluate(() => ({
    origin: self.origin,
    csp: document.querySelector('meta[http-equiv="Content-Security-Policy"]')?.getAttribute('content'),
    cspFirst: (() => {
      const all = [...document.querySelectorAll('meta[http-equiv], script')];
      return all[0]?.getAttribute('http-equiv');
    })(),
  }));
  assert(inside.origin === 'null' && inside.csp === SANDBOX_CSP && inside.cspFirst === 'Content-Security-Policy', 'frame is opaque-origin with the CSP before any script', inside);

  // Rendering + reads.
  await waitText('count', '0 items');
  assert((await text('space'))?.endsWith('(owner)'), 'useSpace has the space from the host', await text('space'));
  assert((await ui.getByRole('heading').filter({ visible: true }).textContent()) === 'Runtime check', 'layout Stack.Screen title');

  // runAsync round trip.
  await visible('new-item').fill('Milk');
  await visible('add').click();
  await waitText('item-1', 'Milk');
  await waitText('status', 'added 1');
  let rows = await platform.rows('SELECT id, name, done FROM items ORDER BY id');
  assert(JSON.stringify(rows) === '[{"id":1,"name":"Milk","done":0}]', 'runAsync wrote to the instance database', rows);

  // useQuery on db_changed from a write the sandbox didn't make.
  const before = (await events(page, 'nav.changed')).length;
  await platform.externalWrite('INSERT INTO items (name) VALUES (?)', ['Eggs']);
  await waitText('item-2', 'Eggs');
  assert(await page.evaluate(() => window.harness.frames.some((f) => f.type === 'db_changed')), 'useQuery re-ran on /ws/platform/events db_changed (external write)');

  // Routes, getFirstAsync, back.
  await visible('item-2').click();
  await waitText('detail-name', 'Eggs');
  await ui.getByRole('heading').filter({ visible: true, hasText: 'Eggs' }).waitFor({ timeout: 10000 });
  assert(true, 'Link -> /item/[id], useLocalSearchParams, getFirstAsync, in-screen Stack.Screen title');
  await visible('toggle').click();
  await waitText('detail-status', 'Done');
  rows = await platform.rows('SELECT done FROM items WHERE id = 2');
  assert(rows[0]?.done === 1, 'toggle (runAsync, variadic bind) wrote; useQuery (named bind) updated');
  await visible('header-back').click();
  await waitText('item-2', 'Eggs ✓');
  const navs = (await events(page, 'nav.changed')).slice(before).map((d) => d.path);
  assert(JSON.stringify(navs) === '["/item/2","/"]', 'nav.changed reached the host', navs);

  // runAction round trip.
  await visible('mark-all').click();
  await waitText('status', '1 marked, 2 done');
  await waitText('item-1', 'Milk ✓');
  rows = await platform.rows('SELECT count(*) AS n FROM items WHERE done = 1');
  assert(rows[0]?.n === 2, 'runAction ran actions/markAllDone.sql on the instance', rows);

  // The host passes nothing but the allowlist to the fixed instance.
  const answers = await frame.evaluate(rawBridge, [
    { id: 9001, method: 'transaction', params: { statements: [{ sql: 'DELETE FROM items' }] } },
    { id: 9002, method: 'db.exec', params: { sql: 'DROP TABLE items' } },
    { id: 9003, method: 'db.getAll', params: { sql: 'SELECT count(*) AS n FROM items', params: [], instance_id: platform.otherInstanceId, instanceId: platform.otherInstanceId } },
    { id: 9004, method: 'db.run', params: { sql: 'CREATE TABLE x (a)' } },
  ]);
  assert(answers[9001]?.error?.code === 'method_not_allowed' && answers[9002]?.error?.code === 'method_not_allowed', 'methods outside the allowlist are refused by the host', answers);
  assert(answers[9003]?.ok && answers[9003].result[0].n === 2, 'an instance id in params is ignored: the host forwards to its fixed instance', answers[9003]);
  assert(answers[9004]?.error?.code === 'sql_not_allowed', 'DDL through db.run is refused by the platform', answers[9004]);

  // Escapes: nothing but the bridge.
  const target = new URL(PROBE_PATH, page.url()).href;
  const results = await frame.evaluate(sandboxProbes, target);
  await page.waitForTimeout(500);
  const escaped = Object.entries(results).filter(([, r]) => r.escaped);
  assert(escaped.length === 0, `no probe escaped (${Object.keys(results).length} probes)`, escaped);
  assert(h.probes.length === 0, 'no probe request left the sandbox', h.probes);
  assert(h.fromSandbox.length === 0, `no request from the sandbox frame got a response (blocked: ${h.blocked.join(', ') || 'none'})`, h.fromSandbox);
  assert(page.url().endsWith('__homeai_runtime_harness__'), 'the host page was not navigated', page.url());

  // Hot reload: app_built -> fetchBundle -> bundle.load, route kept.
  await visible('item-1').click();
  await waitText('detail-name', 'Milk');
  const t0 = Date.now();
  await platform.rebuild({ 'app/index.tsx': fixtureIndex.replace("BUILD = 'v1'", "BUILD = 'v2'") });
  await page.waitForFunction(() => window.harness.events.some((e) => e.event === 'runtime.ready' && e.data.version === 2), null, { timeout: 30000 });
  assert(await visible('detail-name').filter({ hasText: 'Milk' }).isVisible(), `hot reload kept the route (${Date.now() - t0} ms from build request to re-render)`);
  await visible('header-back').click();
  await waitText('build', 'v2');
  assert(true, 'the reloaded bundle is the new build');

  // Self-navigation (accepted risk: it sends one request) kills the frame.
  const requestsBefore = h.probes.length;
  await frame.evaluate((u) => (location.href = `${u}?selfnav`), target).catch(() => {});
  await page.waitForFunction(() => window.harness.killed, null, { timeout: 10000 });
  assert((await page.locator('iframe').count()) === 0, 'a frame that navigates itself is removed by the host');
  assert(h.probes.length - requestsBefore <= 1, 'self-navigation sent at most the one known request', h.probes.slice(requestsBefore));
}

/** A second, read-only host: writes are refused before they reach the platform. */
export async function runViewerChecks(h, platform) {
  const visible = (id) => h.ui.getByTestId(id).filter({ visible: true });
  const count = await platform.rows('SELECT count(*) AS n FROM items');
  await visible('new-item').fill('Nope');
  await visible('add').click();
  await visible('status').filter({ hasText: 'refused: read_only' }).waitFor({ timeout: 10000 });
  const after = await platform.rows('SELECT count(*) AS n FROM items');
  assert(after[0].n === count[0].n, 'a read-only host refuses runAsync (read_only) and nothing is written');
}

export function readFixtureIndex(fixtureDir) {
  return fs.readFileSync(`${fixtureDir}/app/index.tsx`, 'utf8');
}
