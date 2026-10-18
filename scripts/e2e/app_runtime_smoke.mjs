// M12-05: the app runtime page against the live platform, through Caddy
// (see app_runtime_smoke.sh). The host page and checks are the SDK's runtime
// harness (packages/homeai-sdk/tests/harness/harness.mjs), the same ones its
// stub-host test runs; here the backend is a real instance.
//
// Env: RUNTIME_SMOKE_USER / RUNTIME_SMOKE_PASSWORD (an e2e-* user),
// RUNTIME_SMOKE_STATE (a file this writes the app ids to, for the cleanup
// trap), RUNTIME_SMOKE_BASE_URL (default http://localhost).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const SDK = path.resolve(SCRIPT_DIR, '../../packages/homeai-sdk');
const { FIXTURE, importHost } = await import(path.join(SDK, 'tests/helpers.mjs'));
const { openHarness, readFixtureIndex, runChecks, runViewerChecks } = await import(path.join(SDK, 'tests/harness/harness.mjs'));

const BASE = (process.env.RUNTIME_SMOKE_BASE_URL ?? 'http://localhost').replace(/\/$/, '');
const USER = process.env.RUNTIME_SMOKE_USER;
const PASSWORD = process.env.RUNTIME_SMOKE_PASSWORD;
const STATE = process.env.RUNTIME_SMOKE_STATE;
if (!/^e2e-/.test(USER ?? '') || !PASSWORD || !STATE) throw new Error('RUNTIME_SMOKE_USER (e2e-*), RUNTIME_SMOKE_PASSWORD and RUNTIME_SMOKE_STATE are required');

const files = (dir, prefix = '') =>
  fs.readdirSync(path.join(dir, prefix), { withFileTypes: true }).flatMap((d) => {
    const rel = path.posix.join(prefix, d.name);
    return d.isDirectory() ? files(dir, rel) : [rel];
  });

function ok(what) {
  console.log(`ok   ${what}`);
}

const browser = await chromium.launch();
try {
  const context = await browser.newContext();
  // The host page is fulfilled by Playwright, so Chromium's Local Network
  // Access check treats its WebSocket to localhost as a local-network request
  // and blocks it; a page Caddy served itself wouldn't need this.
  await context.grantPermissions(['local-network-access']);
  const api = context.request;
  const call = async (method, url, body, raw) => {
    const r = await api.fetch(`${BASE}${url}`, { method, ...(raw !== undefined ? { data: raw, headers: { 'Content-Type': 'application/octet-stream' } } : body !== undefined ? { data: body } : {}) });
    const text = await r.text();
    if (!r.ok()) throw new Error(`${method} ${url}: ${r.status()} ${text}`);
    return text ? JSON.parse(text) : null;
  };

  await call('POST', '/api/auth/login', { username: USER, password: PASSWORD, device_label: 'e2e app runtime smoke' });
  const personal = (await call('GET', '/api/platform/spaces')).spaces.find((s) => s.kind === 'personal');
  const space = { id: personal.id, slug: personal.slug, name: personal.name, role: personal.role };
  ok(`signed in as ${USER}; personal space ${space.id}`);

  async function upload(slug, rel, content) {
    await call('PUT', `/api/platform/files/content?path=${encodeURIComponent(`/personal/Apps/${slug}/${rel}`)}`, undefined, content);
  }
  const appIds = [];
  async function register(slug, manifest, dir = FIXTURE) {
    for (const rel of files(dir)) {
      await upload(slug, rel, rel === 'app.json' && manifest ? Buffer.from(JSON.stringify(manifest)) : fs.readFileSync(path.join(dir, rel)));
    }
    const { app } = await call('POST', '/api/platform/apps', { source_path: `/personal/Apps/${slug}` });
    appIds.push(app.id);
    fs.writeFileSync(STATE, appIds.join('\n'));
    const instance = await call('POST', `/api/platform/spaces/${space.id}/instances`, { app_id: app.id });
    return { app, instance };
  }
  async function build(app) {
    const r = await call('POST', `/api/platform/apps/${app.id}/build`, {});
    if (!r.ok) throw new Error(`build failed: ${JSON.stringify(r.diagnostics)}`);
    return r;
  }

  const { app, instance } = await register('runtime-check');
  const built = await build(app);
  ok(`built ${app.id} (${built.build.bundle_bytes} B, ${built.build.duration_ms} ms); migrations: ${built.migrations.map((m) => m.migration?.status ?? m.error).join(', ')}`);
  const other = await register('runtime-check-other', { ...JSON.parse(fs.readFileSync(path.join(FIXTURE, 'app.json'), 'utf8')), slug: 'runtime-check-other' });
  ok(`a second instance ${other.instance.id} (unbuilt, no tables) the sandbox must not reach`);

  const bundle = await call('GET', `/api/platform/apps/instances/${instance.id}/bundle`);
  if (!bundle.code.startsWith('__homeai_define(') || bundle.sdk !== '1' || bundle.bundle_id !== built.build.id) throw new Error(`bundle: ${JSON.stringify({ ...bundle, code: bundle.code.slice(0, 40) })}`);
  ok(`GET …/instances/{id}/bundle serves build ${bundle.bundle_id} (${bundle.code.length} chars)`);
  const runtime = await api.get(`${BASE}/app-runtime/1/runtime.js`);
  if (!runtime.ok() || !(runtime.headers()['content-type'] ?? '').includes('javascript')) throw new Error(`runtime: ${runtime.status()} ${runtime.headers()['content-type']}`);
  ok(`GET /app-runtime/1/runtime.js ${runtime.status()} (${(await runtime.body()).length} B, ${runtime.headers()['content-type']})`);
  const missing = await api.get(`${BASE}/app-runtime/9/runtime.js`);
  if (missing.status() !== 404) throw new Error(`/app-runtime/9: ${missing.status()}`);
  ok('an unknown SDK runtime is 404, not the SPA');

  const rpc = (body) => call('POST', `/api/platform/apps/instances/${instance.id}/rpc`, body);
  const platform = {
    externalWrite: (sql, params) => rpc({ op: 'run', sql, params }),
    rows: async (sql) => (await rpc({ op: 'getAll', sql })).rows,
    async rebuild(edits) {
      for (const [rel, content] of Object.entries(edits)) await upload('runtime-check', rel, Buffer.from(content));
      await build(app);
    },
    otherInstanceId: other.instance.id,
  };
  const { SANDBOX_CSP } = await importHost();
  const page = await context.newPage();
  const h = await openHarness(page, { origin: BASE, instanceId: instance.id, space });
  ok(`sandbox document ${await page.evaluate(() => window.harness.srcdocBytes)} chars`);
  await runChecks(h, platform, { SANDBOX_CSP, fixtureIndex: readFixtureIndex(FIXTURE) });

  const viewer = await context.newPage();
  await runViewerChecks(await openHarness(viewer, { origin: BASE, instanceId: instance.id, space: { ...space, role: 'viewer' }, readOnly: true }), platform);

  // M22-02: groups, nested layouts, Tabs, a modal and the icon font, built by the real builder.
  const nav = await register('nav-check', undefined, path.join(path.dirname(FIXTURE), 'nav-check'));
  await build(nav.app);
  const navPage = await context.newPage();
  const n = await openHarness(navPage, { origin: BASE, instanceId: nav.instance.id, space });
  const visible = (id) => n.ui.getByTestId(id).first().waitFor({ state: 'visible', timeout: 10000 });
  await visible('list-screen');
  await visible('tab-settings');
  const font = await n.frame.evaluate(async () => (await document.fonts.load('24px Ionicons')).length > 0 && document.fonts.check('24px Ionicons'));
  if (!font) throw new Error('the Ionicons font did not load in the sandbox');
  ok('nav-check: the first tab, the tab bar, and the icon font loaded under the sandbox CSP');
  await n.ui.getByTestId('tab-settings').click();
  await n.ui.getByTestId('open-advanced').click();
  await visible('advanced-screen');
  await n.ui.getByTestId('tab-index').click();
  await visible('list-screen');
  await n.ui.getByTestId('tab-settings').click();
  await visible('advanced-screen');
  ok('nav-check: each tab keeps its own stack');
  await n.ui.getByTestId('tab-index').click();
  await n.ui.getByTestId('open-new').click();
  await visible('new-screen');
  await visible('save');
  await n.ui.getByTestId('header-close').click();
  await n.ui.getByTestId('new-screen').waitFor({ state: 'detached', timeout: 10000 });
  ok('nav-check: a modal with headerRight opens and closes');
  console.log('PASS: app runtime smoke');
} finally {
  await browser.close();
}
