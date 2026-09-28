// Experiment 2b: the same sandbox inside a real Expo SDK 57 web export
// (expo-host/, react-native-web host rendering <iframe sandbox="allow-scripts">).
//   node build/gen-expo-assets.mjs && (cd expo-host && npx expo export -p web) && node tests/expo-web.e2e.mjs
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';
import { startServer } from '../host/server.mjs';
import { sandboxProbes } from './probes.mjs';
import { check, spike, writeResult } from './lib.mjs';

const expoDist = path.join(spike, 'expo-host/dist');
if (!fs.existsSync(path.join(expoDist, 'index.html'))) throw new Error('run `cd expo-host && npx expo export -p web` first');

const results = [];
const out = { checks: results };
const { server, log, url } = await startServer({ expoDist });
const browser = await chromium.launch();
const report = async (page) => JSON.parse(await page.getByTestId('report').textContent());
try {
  const page = await browser.newPage();
  const t0 = Date.now();
  await page.goto(url);
  await page.waitForFunction(() => document.querySelector('[data-testid=report]')?.textContent.includes('ready_v1'), null, { timeout: 15000 });
  out.firstRenderMs = Date.now() - t0;
  const iframe = page.locator('iframe');
  check(results, 'Expo web host renders <iframe sandbox="allow-scripts">', (await iframe.getAttribute('sandbox')) === 'allow-scripts');
  const frame = page.frames().find((f) => f !== page.mainFrame());
  await frame.getByText('Bread').waitFor({ timeout: 5000 });
  await frame.getByTestId('item-3').click();
  await frame.getByTestId('detail-name').filter({ hasText: 'Bread' }).waitFor({ timeout: 5000 });
  await frame.getByTestId('toggle').click();
  await frame.getByTestId('detail-status').filter({ hasText: 'Done' }).waitFor({ timeout: 5000 });
  check(results, 'app renders, navigates to /item/3, writes through host RPC', (await report(page)).lastPath === '/item/3');

  await page.getByTestId('btn-bench').click();
  await page.waitForFunction(() => document.querySelector('[data-testid=report]').textContent.includes('latencyMs_db.getAll'), null, { timeout: 30000 });
  const r = await report(page);
  out.latencyMs = { echo: r['latencyMs_echo'], dbGetAll: r['latencyMs_db.getAll'] };
  check(results, 'bench: 200 echo + 200 db.getAll round trips', r['latencyMs_echo'].n === 200 && r['latencyMs_db.getAll'].n === 200, out.latencyMs);

  await page.evaluate(() => (window.__marker = 1));
  await page.getByTestId('btn-reload').click();
  await frame.getByText('Toggle done (v2)').waitFor({ timeout: 5000 });
  await page.waitForFunction(() => document.querySelector('[data-testid=report]').textContent.includes('hotReloadMs'));
  const r2 = await report(page);
  out.hotReloadMs = r2.hotReloadMs;
  check(results, 'hot reload keeps host page and route', (await page.evaluate(() => window.__marker)) === 1 && r2.lastPath === '/item/3', { hotReloadMs: r2.hotReloadMs });

  log.length = 0;
  const probes = await frame.evaluate(sandboxProbes, url);
  await page.waitForTimeout(300);
  out.probes = probes;
  for (const k of ['document.cookie', 'localStorage', 'parent.document', 'fetch host /api/secret credentials:include', 'fetch host /api/rpc credentials:include', 'image beacon']) {
    check(results, `sandbox cannot: ${k}`, !probes[k].succeeded, probes[k].error);
  }
  check(results, 'no sandbox request reached the server', log.filter((l) => l.origin !== url).length === 0, log);
} finally {
  writeResult('expo-web', out);
  await browser.close();
  server.close();
}
const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exitCode = failed.length ? 1 : 0;
