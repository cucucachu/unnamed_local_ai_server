// The runtime page in Chromium with a stub host: the harness host page and
// checks (tests/harness/harness.mjs) against a stand-in platform served with
// page.route at a fake origin, its events pushed into the host page (not
// routeWebSocket: that fakes WebSocket inside every frame, the sandbox's
// too, past its CSP). Nothing else is reachable, so any request the sandbox
// makes shows up. scripts/e2e/app_runtime_smoke.sh
// runs the same checks against the live platform.
//   npm test    (needs Playwright's Chromium: npx playwright install chromium)
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { chromium } from 'playwright';
import { APP_ID, FIXTURE, INSTANCE_ID, RUNTIME, bundleApp, importHost, stubPlatform, until } from './helpers.mjs';
import { HOST_PATH, PROBE_PATH, openHarness, readFixtureIndex, runChecks, runViewerChecks } from './harness/harness.mjs';

const ORIGIN = 'http://homeai.test';
const SPACE = { id: '33333333-3333-4333-8333-333333333333', slug: 'personal', name: 'Personal', role: 'owner' };

/** Frames the platform would send on /ws/platform/events, handed to the host page. */
function pushEvents(page, platform) {
  const push = (frame) => page.evaluate((w) => window.harness.deliver(w), JSON.stringify(frame)).catch(() => {});
  return async () => {
    await push({ type: 'ready' });
    platform.onEvent(push);
  };
}

async function stubHost(page, platform, state, appDir = FIXTURE) {
  const unexpected = [];
  await page.route('**/*', (route) => {
    unexpected.push(route.request().url());
    return route.abort();
  });
  const served = [];
  await page.route(`${ORIGIN}/app-runtime/1/*.js`, (route) => {
    const name = path.basename(new URL(route.request().url()).pathname);
    served.push(name);
    return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(path.join(path.dirname(RUNTIME), name), 'utf8') });
  });
  page.served = served;
  await page.route(`${ORIGIN}/api/platform/apps/instances/${INSTANCE_ID}/bundle`, async (route) =>
    route.fulfill({ json: { app_id: APP_ID, version: '1.0.0', sdk: '1', bundle_id: `b${state.build}`, code: await bundleApp(appDir, { dev: false, edits: state.edits }) } }),
  );
  await page.route(`${ORIGIN}/api/platform/apps/instances/${INSTANCE_ID}/rpc`, (route) => {
    const r = platform.rpc(route.request().postDataJSON());
    return route.fulfill({ status: r.status, json: r.body });
  });
  return unexpected;
}

test('the runtime page with a stub host: render, routes, bridge round trips, live queries, hot reload, containment', async () => {
  const { SANDBOX_CSP } = await importHost();
  const browser = await chromium.launch();
  try {
    const platform = stubPlatform();
    const state = { build: 1, edits: {} };
    const page = await browser.newPage();
    const unexpected = await stubHost(page, platform, state);
    const h = await openHarness(page, { origin: ORIGIN, instanceId: INSTANCE_ID, space: SPACE, events: 'push', onReady: pushEvents(page, platform) });
    await runChecks(
      h,
      {
        externalWrite: async (sql, params) => platform.externalWrite(sql, params),
        rows: async (sql) => platform.db.prepare(sql).all().map((r) => ({ ...r })),
        rebuild: async (edits) => {
          state.build++;
          state.edits = edits;
          platform.emit({ type: 'app_built', app_id: APP_ID, version: '1.0.0' });
        },
        otherInstanceId: '44444444-4444-4444-8444-444444444444',
      },
      { SANDBOX_CSP, fixtureIndex: readFixtureIndex(FIXTURE) },
    );
    const stray = unexpected.filter((u) => !u.startsWith(`${ORIGIN}${HOST_PATH}`) && !u.startsWith(`${ORIGIN}${PROBE_PATH}`));
    if (stray.length) throw new Error(`requests nothing should make: ${stray.join(', ')}`);

    const viewer = await browser.newPage();
    await stubHost(viewer, platform, { build: 1, edits: {} });
    await runViewerChecks(await openHarness(viewer, { origin: ORIGIN, instanceId: INSTANCE_ID, space: { ...SPACE, role: 'viewer' }, readOnly: true, events: 'push', onReady: pushEvents(viewer, platform) }), {
      rows: async (sql) => platform.db.prepare(sql).all().map((r) => ({ ...r })),
    });
  } finally {
    await browser.close();
  }
});

test('GLView and three in Chromium: only bundles that import three get it, the cube draws, and a quick tap reaches onPressIn', async () => {
  const GL = path.join(path.dirname(FIXTURE), 'gl-check');
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const unexpected = await stubHost(page, stubPlatform(GL), { build: 1, edits: {} }, GL);
    const h = await openHarness(page, { origin: ORIGIN, instanceId: INSTANCE_ID, space: SPACE, events: 'push', onReady: async () => page.evaluate(() => window.harness.deliver(JSON.stringify({ type: 'ready' }))) });
    assert.deepEqual(page.served.sort(), ['runtime.js', 'three.js']);
    const pixel = h.ui.getByTestId('pixel');
    const seen = await until('a few frames', async () => {
      const t = await pixel.textContent();
      const m = /^(\d+),(\d+),(\d+),(\d+) frame (\d+) size (\d+)x(\d+)$/.exec(t ?? '');
      return m && Number(m[5]) > 20 && m.slice(1).map(Number);
    }, 10000);
    const [r, g, b, a, , w, hgt] = seen;
    assert.ok(r > 200 && g < 40 && b < 40 && a === 255, `centre pixel ${seen.slice(0, 4)}`);
    assert.ok(w >= 400 && hgt >= 500, `drawing buffer ${w}x${hgt}`);
    // A tap quicker than react-native-web's 50 ms press delay, with only onPressIn, as a game's drop.
    const box = await h.ui.getByTestId('stage').boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.up();
    await until('onPressIn', async () => (await h.ui.getByTestId('taps').textContent()) === '1');
    assert.deepEqual(unexpected.filter((u) => !u.startsWith(`${ORIGIN}${HOST_PATH}`)), []);
    const errors = await page.evaluate(() => window.harness.events.filter((e) => e.event === 'runtime.error'));
    assert.deepEqual(errors, []);

    const plain = await browser.newPage();
    await stubHost(plain, stubPlatform(), { build: 1, edits: {} });
    await openHarness(plain, { origin: ORIGIN, instanceId: INSTANCE_ID, space: SPACE, events: 'push', onReady: async () => plain.evaluate(() => window.harness.deliver(JSON.stringify({ type: 'ready' }))) });
    assert.deepEqual(plain.served, ['runtime.js']);
  } finally {
    await browser.close();
  }
});
