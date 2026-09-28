// M12-06: the web app's Apps tab and app runner against the live stack,
// through Caddy (see app_runner_browser_smoke.sh for the flow).
//
// Env: RUNNER_SMOKE_USER / RUNNER_SMOKE_PASSWORD (the owner) and
// RUNNER_SMOKE_VIEWER / RUNNER_SMOKE_VIEWER_PASSWORD (e2e-* users),
// RUNNER_SMOKE_SPACE_SLUG (e2e-runner-*), RUNNER_SMOKE_STATE (a file this
// writes the app id to, for the cleanup trap), RUNNER_SMOKE_BASE_URL
// (default http://localhost/).
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

import { FIXTURE, fixtureFiles, fixtureV2 } from './app_fixture.mjs';
import { loginThroughUi } from './auth_helpers.mjs';

const BASE = (process.env.RUNNER_SMOKE_BASE_URL ?? 'http://localhost/').replace(/\/$/, '');
const OWNER = { username: process.env.RUNNER_SMOKE_USER, password: process.env.RUNNER_SMOKE_PASSWORD };
const VIEWER = { username: process.env.RUNNER_SMOKE_VIEWER, password: process.env.RUNNER_SMOKE_VIEWER_PASSWORD };
const SLUG = process.env.RUNNER_SMOKE_SPACE_SLUG;
const STATE = process.env.RUNNER_SMOKE_STATE;
if (![OWNER.username, VIEWER.username].every((u) => /^e2e-/.test(u ?? '')) || !OWNER.password || !VIEWER.password || !/^e2e-runner-/.test(SLUG ?? '') || !STATE) {
  throw new Error('RUNNER_SMOKE_USER / _VIEWER (e2e-*), their passwords, RUNNER_SMOKE_SPACE_SLUG (e2e-runner-*) and RUNNER_SMOKE_STATE are required');
}
const UI_TIMEOUT = 30_000;
const APP_DIR = `/spaces/${SLUG}/Apps/runtime-check`;
const ROW = `Eggs ${process.pid}`;

const ok = (what) => console.log(`ok   ${what}`);
function check(cond, what, detail) {
  if (!cond) throw new Error(`FAIL: ${what}${detail === undefined ? '' : `: ${JSON.stringify(detail)}`}`);
  ok(what);
}

/** REST as the context's signed-in user (the page's own cookie). */
function api(context) {
  return async (method, url, { json, raw, timeout = 30_000 } = {}) => {
    const r = await context.request.fetch(`${BASE}${url}`, {
      method,
      timeout,
      ...(raw !== undefined ? { data: raw, headers: { 'Content-Type': 'application/octet-stream' } } : json !== undefined ? { data: json } : {}),
    });
    const text = await r.text();
    if (!r.ok()) throw new Error(`${method} ${url}: ${r.status()} ${text}`);
    return text ? JSON.parse(text) : null;
  };
}

async function signIn(browser, user) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${BASE}/`);
  await loginThroughUi(page, user);
  return { context, page, call: api(context) };
}

const APP_FRAME = 'iframe[title="app"]';

/** Apps tab -> the space's section -> the app -> its runner, rendered. */
async function openFromAppsTab(page) {
  await page.getByRole('tab', { name: 'Home' }).click();
  const section = page.getByTestId(`apps-space-${SLUG}`);
  await section.waitFor({ timeout: UI_TIMEOUT });
  await section.getByTestId('apps-open-runtime-check').click();
  await page.getByTestId('app-runner').waitFor({ timeout: UI_TIMEOUT });
  await page.frameLocator(APP_FRAME).getByTestId('build').waitFor({ timeout: UI_TIMEOUT });
  return section;
}

const ui = (page) => page.frameLocator(APP_FRAME);
const frameText = (page, testID) => ui(page).getByTestId(testID).innerText({ timeout: UI_TIMEOUT });
async function waitForFrameText(page, testID, predicate, what) {
  const deadline = Date.now() + UI_TIMEOUT;
  let last = null;
  while (Date.now() < deadline) {
    last = await ui(page).getByTestId(testID).innerText({ timeout: 2000 }).catch(() => null);
    if (last !== null && predicate(last)) return last;
    await page.waitForTimeout(150);
  }
  throw new Error(`FAIL: ${what} (last ${testID}: ${JSON.stringify(last)})`);
}

/** Every request a sandbox frame issued that got a response. */
function watchSandboxRequests(page, into) {
  page.on('requestfinished', (r) => {
    if (r.frame() !== page.mainFrame()) into.push(r.url());
  });
}

const browser = await chromium.launch();
try {
  const fromSandbox = [];
  const owner = await signIn(browser, OWNER);
  watchSandboxRequests(owner.page, fromSandbox);
  ok(`signed in as ${OWNER.username} through the login form`);

  // --- setup over REST: shared space, viewer, the fixture app built --------
  const space = await owner.call('POST', '/api/platform/spaces', { json: { slug: SLUG, name: `E2E Runner ${process.pid}` } });
  const directory = await owner.call('GET', '/api/platform/users/directory');
  const viewerUser = directory.users.find((u) => u.username === VIEWER.username);
  await owner.call('POST', `/api/platform/spaces/${space.id}/members`, { json: { user_id: viewerUser.id, role: 'viewer' } });
  for (const rel of fixtureFiles()) {
    await owner.call('PUT', `/api/platform/files/content?path=${encodeURIComponent(`${APP_DIR}/${rel}`)}`, { raw: fs.readFileSync(path.join(FIXTURE, rel)) });
  }
  const { app } = await owner.call('POST', '/api/platform/apps', { json: { source_path: APP_DIR } });
  fs.writeFileSync(STATE, `${app.id}\n`);
  const instance = await owner.call('POST', `/api/platform/spaces/${space.id}/instances`, { json: { app_id: app.id } });
  const build = async () => {
    const r = await owner.call('POST', `/api/platform/apps/${app.id}/build`, { json: {}, timeout: 180_000 });
    if (!r.ok) throw new Error(`build failed: ${JSON.stringify(r.diagnostics)}`);
    return r;
  };
  const built = await build();
  ok(`space ${SLUG} (viewer ${VIEWER.username}); app ${app.id} built (${built.build.bundle_bytes} B), instance ${instance.id}`);
  const rows = async () =>
    (await owner.call('POST', `/api/platform/apps/instances/${instance.id}/rpc`, { json: { op: 'getAll', sql: 'SELECT name FROM items ORDER BY id' } })).rows.map((r) => r.name);

  // --- 1. Apps tab -> runner, in the sandboxed frame -----------------------
  const { page } = owner;
  const section = await openFromAppsTab(page);
  check((await section.innerText()).includes('Runtime check'), 'the Apps tab lists the instance under its space');
  check(new URL(page.url()).pathname === `/apps/${instance.id}`, 'the runner is at /apps/<instance id>', page.url());
  const sandboxAttr = await page.locator(APP_FRAME).getAttribute('sandbox');
  check(sandboxAttr === 'allow-scripts', 'the app runs in <iframe sandbox="allow-scripts"> (no allow-same-origin)', sandboxAttr);
  check((await frameText(page, 'build')) === 'v1', 'the app rendered build v1');
  check((await frameText(page, 'space')).includes('(owner)'), "the sandbox got the owner's space");

  // --- 2. write a row, reload, still there ---------------------------------
  await ui(page).getByTestId('new-item').fill(ROW);
  await ui(page).getByTestId('add').click();
  await waitForFrameText(page, 'status', (t) => /^added \d+$/.test(t), 'the write was acknowledged');
  await waitForFrameText(page, 'count', (t) => t === '1 items', 'the live query shows the new row');
  check((await rows()).includes(ROW), 'the row is in the instance database (REST getAll)', await rows());
  await page.reload();
  await page.frameLocator(APP_FRAME).getByTestId('build').waitFor({ timeout: UI_TIMEOUT });
  await waitForFrameText(page, 'count', (t) => t === '1 items', 'after a page reload the app still shows one row');
  check((await ui(page).getByText(ROW).count()) === 1, `after a page reload the row "${ROW}" is shown`);

  // --- 2b. Ask the agent panel with app context; a write shows up live ------
  await page.getByTestId('app-ask-agent').click();
  await page.getByTestId('app-agent-panel').waitFor({ timeout: UI_TIMEOUT });
  const context = await page.getByTestId('app-agent-context').innerText();
  check(context.includes(instance.id), 'the panel is seeded with the instance id', context);
  check(context.includes(SLUG), 'the panel is seeded with the space slug', context);
  check(/Runtime check/.test(context) && /CREATE TABLE items/.test(context), 'the panel loaded app.json, AGENT.md and schema.sql', context);
  await owner.call('POST', `/api/platform/apps/instances/${instance.id}/rpc`, {
    json: { op: 'run', sql: 'INSERT INTO items (name) VALUES (?)', params: ['From agent'] },
  });
  await waitForFrameText(page, 'count', (t) => t === '2 items', 'db_changed updated the running app while the panel was open');
  await page.getByTestId('app-agent-close').click();
  await page.getByTestId('app-agent-panel').waitFor({ state: 'detached', timeout: UI_TIMEOUT });
  ok('the agent panel closed');

  // --- 3. a rebuild hot-reloads the running app ----------------------------
  await page.locator(APP_FRAME).evaluate((f) => (f.dataset.e2e = 'first-frame'));
  await owner.call('PUT', `/api/platform/files/content?path=${encodeURIComponent(`${APP_DIR}/app/index.tsx`)}`, { raw: Buffer.from(fixtureV2()) });
  const t0 = Date.now();
  await build();
  await waitForFrameText(page, 'build', (t) => t === 'v2', 'the rebuild hot-reloaded the app to v2');
  ok(`hot reload visible ${Date.now() - t0} ms after the build request started`);
  check((await page.locator(APP_FRAME).evaluate((f) => f.dataset.e2e)) === 'first-frame', 'hot reload kept the same frame (no remount)');
  check((await ui(page).getByText(ROW).count()) === 1, 'the row is still shown after the hot reload');

  // --- 4. a runtime error -> the host's overlay -> Reload ------------------
  await ui(page).getByTestId('crash').click();
  await page.getByTestId('app-error-overlay').waitFor({ timeout: UI_TIMEOUT });
  const message = await page.getByTestId('app-error-message').innerText();
  check(message.includes('e2e runner crash'), 'a runtime error in the app shows the error overlay with its message', message);
  await page.getByTestId('app-error-reload').click();
  await page.getByTestId('app-error-overlay').waitFor({ state: 'detached', timeout: UI_TIMEOUT });
  await waitForFrameText(page, 'build', (t) => t === 'v2', 'Reload brings the app back (v2)');
  check((await page.locator(APP_FRAME).evaluate((f) => f.dataset.e2e ?? null)) === null, 'Reload started a fresh frame');
  check((await ui(page).getByText(ROW).count()) === 1, 'the row is shown after Reload');

  // --- 5. the viewer: read-only ---------------------------------------------
  const viewer = await signIn(browser, VIEWER);
  watchSandboxRequests(viewer.page, fromSandbox);
  const viewerSection = await openFromAppsTab(viewer.page);
  check((await viewerSection.innerText()).includes('View only'), 'the viewer sees the app marked "View only"');
  await viewer.page.getByTestId('app-runner-read-only').waitFor({ timeout: UI_TIMEOUT });
  ok('the runner says the viewer can only view');
  check((await frameText(viewer.page, 'space')).includes('(viewer)'), "the viewer's sandbox got the viewer role");
  check((await ui(viewer.page).getByText(ROW).count()) === 1, 'the viewer sees the row');
  await ui(viewer.page).getByTestId('new-item').fill('viewer write');
  await ui(viewer.page).getByTestId('add').click();
  await waitForFrameText(viewer.page, 'status', (t) => t === 'refused: read_only', "the viewer's write is refused (read_only)");
  const after = await rows();
  check(
    after.includes(ROW) && after.includes('From agent') && !after.includes('viewer write'),
    'the database is unchanged by the viewer',
    after,
  );

  check(fromSandbox.length === 0, 'no request from a sandbox frame reached anything', fromSandbox);
  await viewer.context.close();
  await owner.context.close();
  console.log('PASS: app runner browser smoke');
} finally {
  await browser.close();
}
