// Experiment 2: sandboxed iframe on web (Chromium via Playwright).
//   node tests/sandbox.e2e.mjs      -> results/sandbox.json, exit 1 on any FAIL
import { chromium } from 'playwright';
import { startServer } from '../host/server.mjs';
import { compileApp } from '../build/compile-app.mjs';
import { sandboxProbes } from './probes.mjs';
import { appVariant, check, ensureRuntime, stats, writeResult } from './lib.mjs';

const results = [];
const runtime = await ensureRuntime();
const { server, log, url } = await startServer();
const browser = await chromium.launch();
const out = { runtime: runtime.stats, checks: results };

async function openHost(query = '') {
  const page = await browser.newPage();
  const consoleErrors = [];
  page.on('console', (m) => m.type() === 'error' && consoleErrors.push(m.text()));
  await fetch(`${url}/api/_reset`);
  const t0 = Date.now();
  await page.goto(`${url}/${query}`);
  await page.waitForFunction(() => window.homeHost?.events.some((e) => e.event === 'runtime.ready'), null, { timeout: 15000 });
  const firstRenderMs = Date.now() - t0;
  const frame = page.frames().find((f) => f !== page.mainFrame());
  return { page, frame, consoleErrors, firstRenderMs };
}

try {
  // --- render + navigation ------------------------------------------------
  const { page, frame, firstRenderMs } = await openHost();
  out.firstRenderMs = firstRenderMs;
  out.srcdocBytes = await page.evaluate(() => window.homeHost.srcdocBytes);
  const sandboxAttr = await page.getAttribute('iframe', 'sandbox');
  check(results, 'iframe sandbox attribute is exactly "allow-scripts"', sandboxAttr === 'allow-scripts', sandboxAttr);
  await frame.getByText('Eggs').waitFor({ timeout: 5000 });
  check(results, 'index route renders rows from db.getAll via host RPC', await frame.getByText('Milk').isVisible());
  check(results, 'layout Stack.Screen title on index', (await frame.getByRole('heading').first().textContent()) === 'Groceries');

  await frame.getByTestId('item-2').click();
  await frame.getByTestId('detail-name').filter({ hasText: 'Eggs' }).waitFor({ timeout: 5000 });
  const heading = await frame.getByRole('heading').filter({ visible: true }).textContent();
  check(results, 'Link to /item/[id] navigates; useLocalSearchParams id=2; in-screen Stack.Screen sets title', heading === 'Eggs', heading);
  await frame.getByTestId('toggle').click();
  await frame.getByTestId('detail-status').filter({ hasText: 'Done' }).waitFor({ timeout: 5000 });
  check(results, 'db.run + db.changed relay re-runs useQuery', true);
  await frame.getByTestId('header-back').filter({ visible: true }).click();
  await frame.getByTestId('new-item').filter({ visible: true }).waitFor();
  await frame.getByTestId('new-item').fill('Apples');
  await frame.getByTestId('add').click();
  await frame.getByText('Apples').waitFor({ timeout: 5000 });
  check(results, 'back() returns to index; insert via runAsync appears', true);
  const navs = await page.evaluate(() => window.homeHost.events.filter((e) => e.event === 'nav.changed').map((e) => e.data.path));
  check(results, 'nav.changed events reported to host', JSON.stringify(navs) === JSON.stringify(['/', '/item/2', '/']), navs);

  // --- RPC latency ----------------------------------------------------------
  await page.evaluate(() => window.homeHost.bench(20));
  const echo = await page.evaluate(() => window.homeHost.bench(200, 'echo'));
  await page.evaluate(() => window.homeHost.bench(20, 'db.getAll'));
  const dbAll = await page.evaluate(() => window.homeHost.bench(200, 'db.getAll'));
  out.latencyMs = { echo_bridge_only: stats(echo), db_getAll_via_host_http: stats(dbAll) };
  console.log('latency', JSON.stringify(out.latencyMs));
  check(results, 'bridge echo p95 < 5 ms', out.latencyMs.echo_bridge_only.p95 < 5, out.latencyMs.echo_bridge_only);

  // --- hot reload -----------------------------------------------------------
  await frame.getByTestId('item-1').click();
  await frame.getByTestId('detail-name').filter({ hasText: 'Milk' }).waitFor();
  await page.evaluate(() => (window.__hostMarker = 'still-here'));
  const variant = appVariant('groceries', 'app/item/[id].tsx', "Toggle done", 'Toggle done (v2)');
  const v2 = await compileApp(variant);
  const reload = await page.evaluate((code) => window.homeHost.pushBundle(code), v2.code);
  await frame.getByText('Toggle done (v2)').waitFor({ timeout: 5000 });
  const marker = await page.evaluate(() => window.__hostMarker);
  const detail = await frame.getByTestId('detail-name').filter({ visible: true }).textContent();
  out.hotReload = { pushToReadyMs: +reload.ms.toFixed(1), renderMs: +reload.renderMs.toFixed(1), compileMs: v2.stats.compileMs };
  check(results, 'hot reload: new bundle renders without host reload, route /item/1 kept', marker === 'still-here' && detail === 'Milk', out.hotReload);

  // --- escape probes (with CSP, the design) ----------------------------------
  log.length = 0;
  const probes = await frame.evaluate(sandboxProbes, url);
  await page.waitForTimeout(500);
  out.probes_with_csp = { probes, serverSawFromSandbox: log.filter((l) => l.origin !== url).map((l) => ({ path: l.path, origin: l.origin, hasSession: l.hasSession })) };
  const mustFail = ['document.cookie', 'localStorage', 'sessionStorage', 'indexedDB', 'parent.document', 'parent.location.href', 'parent.homeHost',
    'fetch host /api/secret credentials:include', 'fetch host /api/rpc credentials:include', 'fetch no-cors', 'XMLHttpRequest', 'WebSocket', 'image beacon', 'window.open'];
  for (const k of mustFail) check(results, `[csp] sandbox cannot: ${k}`, !probes[k].succeeded, probes[k].error ?? probes[k].value);
  check(results, '[csp] origin is opaque ("null")', probes['self.origin'].value === 'null');
  check(results, '[csp] top navigation blocked (host URL unchanged)', !page.url().includes('pwned'), page.url());
  check(results, '[csp] no request from the sandbox reached the host server', out.probes_with_csp.serverSawFromSandbox.length === 0, out.probes_with_csp.serverSawFromSandbox);
  await page.close();

  // --- same probes, sandbox attribute only (no CSP) ------------------------
  const noCsp = await openHost('?csp=0');
  log.length = 0;
  const probes2 = await noCsp.frame.evaluate(sandboxProbes, url);
  await noCsp.page.waitForTimeout(500);
  // The host page's own RPC fetches (Origin = host) are legitimately credentialed; everything else came from the sandbox.
  const seen = log.filter((l) => l.origin !== url).map((l) => ({ path: l.path, origin: l.origin, hasSession: l.hasSession }));
  out.probes_without_csp = { probes: probes2, serverSawFromSandbox: seen };
  for (const k of ['document.cookie', 'localStorage', 'parent.document', 'fetch host /api/secret credentials:include', 'fetch host /api/rpc credentials:include']) {
    check(results, `[no csp] sandbox cannot: ${k}`, !probes2[k].succeeded, probes2[k].error ?? probes2[k].value);
  }
  check(results, '[no csp] requests that did reach the host carried no session cookie', seen.every((l) => !l.hasSession), seen);
  await noCsp.page.close();

  // --- self-navigation (exfil via frame location) ---------------------------
  const nav = await openHost();
  log.length = 0;
  await nav.frame.evaluate((u) => { location.href = u + '/exfil?data=secret-row'; }, url).catch(() => {});
  await nav.page.waitForTimeout(800);
  const killed = await nav.page.evaluate(() => window.homeHost.events.some((e) => e.event === 'host.killed'));
  const exfilHit = log.find((l) => l.path.startsWith('/exfil'));
  out.selfNavigation = { requestReachedServer: !!exfilHit, hasSession: exfilHit?.hasSession ?? null, hostKilledFrame: killed };
  check(results, 'self-navigation: host detects the second load and removes the frame', killed, out.selfNavigation);
  await nav.page.close();

  // --- viewer role: host refuses writes ---------------------------------------
  const viewer = await openHost('?role=viewer');
  await viewer.frame.getByTestId('new-item').fill('Nope');
  await viewer.frame.getByTestId('add').click();
  await viewer.page.waitForTimeout(300);
  check(results, 'viewer: host rejects db.run (row not added)', !(await viewer.frame.getByText('Nope', { exact: true }).count()));
  await viewer.page.close();
} finally {
  writeResult('sandbox', out);
  await browser.close();
  server.close();
}
const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exitCode = failed.length ? 1 : 0;
