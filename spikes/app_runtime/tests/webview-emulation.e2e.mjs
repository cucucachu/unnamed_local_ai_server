// Experiment 3 (desktop part): the exact sandbox HTML the phone gets, loaded as
// a top-level opaque-origin document (like WebView source={{ html }}), with
// react-native-webview 13.16.1's bridge semantics emulated:
//   page -> host : window.ReactNativeWebView.postMessage(string)   (injected before content)
//   host -> page : injectJavaScript(injectScriptFor(env))            (same string as Sandbox.native.tsx)
// Host logic is expo-host/bridgeHost.ts itself. This does NOT prove Expo Go
// works — that's the maintainer's manual step (README "Expo Go").
//   node tests/webview-emulation.e2e.mjs
import { DatabaseSync } from 'node:sqlite';
import { chromium } from 'playwright';
import { buildRuntime } from '../build/build-runtime.mjs';
import { compileApp } from '../build/compile-app.mjs';
import { sandboxHtml } from '../build/sandbox-html.mjs';
import { injectScriptFor, makeHost } from '../expo-host/bridgeHost.ts';
import { sandboxProbes } from './probes.mjs';
import { appVariant, check, spike, stats, writeResult } from './lib.mjs';

const results = [];
const out = { checks: results, note: 'Emulation in desktop Chromium; latency includes a CDP round trip per host->page message and is not representative of a phone.' };
const runtime = await buildRuntime();
const app = await compileApp(`${spike}/apps/groceries`);
const html = sandboxHtml({ runtime: runtime.code, app: app.code, config: { initialPath: '/', debug: true } });

const db = new DatabaseSync(':memory:');
db.exec(`CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0); INSERT INTO items (name) VALUES ('Milk'), ('Eggs'), ('Bread');`);
async function rpc(method, params) {
  const st = db.prepare(params.sql);
  const args = params.params ?? [];
  if (method === 'db.getAll') return st.all(...args);
  if (method === 'db.getFirst') return st.get(...args) ?? null;
  const r = st.run(...args);
  return { changes: Number(r.changes), lastInsertRowId: Number(r.lastInsertRowid) };
}

const browser = await chromium.launch();
const page = await browser.newPage();
const waiters = {};
const events = [];
const host = makeHost(
  (env) => page.evaluate(injectScriptFor(env)).catch(() => {}),
  rpc,
  (event, data) => {
    events.push({ event, data });
    waiters[event]?.(data);
    delete waiters[event];
  },
);
const waitFor = (event) => new Promise((resolve) => (waiters[event] = resolve));
await page.exposeFunction('__rnwNativeOnMessage', (s) => host.receive(s));
await page.addInitScript(() => {
  window.ReactNativeWebView = { postMessage: (data) => window.__rnwNativeOnMessage(String(data)) };
});

try {
  const ready = Promise.race([waitFor('runtime.ready'), new Promise((_, rej) => setTimeout(() => rej(new Error('no runtime.ready')), 15000))]);
  const t0 = Date.now();
  // data: keeps init scripts (setContent's document.write doesn't) and, like
  // loadDataWithBaseURL with no base URL, yields an opaque origin.
  await page.goto(`data:text/html;base64,${Buffer.from(html).toString('base64')}`);
  await ready;
  out.firstRenderMs = Date.now() - t0;
  check(results, 'top-level document has an opaque origin like source={{html}}', (await page.evaluate(() => self.origin)) === 'null');
  await page.getByText('Eggs').waitFor({ timeout: 5000 });
  await page.getByTestId('item-2').click();
  await page.getByTestId('detail-name').filter({ hasText: 'Eggs' }).waitFor({ timeout: 5000 });
  await page.getByTestId('toggle').click();
  await page.getByTestId('detail-status').filter({ hasText: 'Done' }).waitFor({ timeout: 5000 });
  check(results, 'renders + navigates + writes over ReactNativeWebView.postMessage / injectJavaScript', true);

  for (const method of ['echo', 'db.getAll']) {
    host.event('bench.run', { n: 20, method });
    await waitFor('bench.result');
    host.event('bench.run', { n: 200, method });
    const { samples } = await waitFor('bench.result');
    out[`latencyMs_${method}`] = stats(samples);
  }
  check(results, 'bench 200 round trips (emulated transport)', out['latencyMs_echo'].n === 200, { echo: out['latencyMs_echo'], dbGetAll: out['latencyMs_db.getAll'] });

  // Android's WebView.postMessage() dispatches a MessageEvent on `document`,
  // iOS on `window`; the runtime deliberately ignores both (injectJavaScript only).
  const before = events.length;
  await page.evaluate(() => {
    const env = JSON.stringify({ homeai: 1, kind: 'evt', event: 'bench.run', data: { n: 1 } });
    document.dispatchEvent(new MessageEvent('message', { data: env }));
    window.dispatchEvent(new MessageEvent('message', { data: env }));
  });
  await page.waitForTimeout(200);
  check(results, 'RN WebView.postMessage-style MessageEvents are ignored (single, platform-neutral inbound path)', events.length === before);

  const v2 = await compileApp(appVariant('groceries', 'app/item/[id].tsx', 'Toggle done', 'Toggle done (v2)'));
  const t1 = Date.now();
  const r2 = waitFor('runtime.ready');
  host.event('bundle.load', { code: v2.code });
  const d2 = await r2;
  out.hotReloadMs = Date.now() - t1;
  await page.getByText('Toggle done (v2)').waitFor({ timeout: 5000 });
  check(results, 'hot reload via injectJavaScript(bundle.load) keeps route', d2.version === 2 && (await page.getByTestId('detail-name').filter({ visible: true }).textContent()) === 'Eggs', { ms: out.hotReloadMs, bundleBytes: v2.stats.bytes });

  const probes = await page.evaluate(sandboxProbes, 'https://example.com');
  out.probes = probes;
  for (const k of ['fetch host /api/secret credentials:include', 'XMLHttpRequest', 'WebSocket', 'image beacon', 'fetch no-cors']) {
    check(results, `CSP blocks network: ${k}`, !probes[k].succeeded, probes[k].error);
  }
} finally {
  writeResult('webview-emulation', out);
  await browser.close();
}
const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exitCode = failed.length ? 1 : 0;
