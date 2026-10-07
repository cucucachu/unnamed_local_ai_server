// M12-07: the reference Grocery list app through Caddy + the Apps tab, in
// headless Chromium. Driven by grocery_app_smoke.sh (which makes the users
// and cleans up); see its header for what is checked.
//
// Env: GROCERY_SMOKE_USER / _EDITOR / _VIEWER and their _PASSWORDs (e2e-*
// users), GROCERY_SMOKE_SPACE_SLUG (e2e-grocery-*), GROCERY_SMOKE_STATE (a
// file this writes the app ids to, for the cleanup trap),
// GROCERY_SMOKE_BASE_URL (default http://localhost/).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

import { fixtureFiles } from './app_fixture.mjs';
import { loginThroughUi } from './auth_helpers.mjs';
import { openSpacePage } from './nav_helpers.mjs';

const APP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../examples/apps/grocery-list');
const APP_SLUG = 'grocery-list';
const BASE = (process.env.GROCERY_SMOKE_BASE_URL ?? 'http://localhost/').replace(/\/$/, '');
const OWNER = { username: process.env.GROCERY_SMOKE_USER, password: process.env.GROCERY_SMOKE_PASSWORD };
const EDITOR = { username: process.env.GROCERY_SMOKE_EDITOR, password: process.env.GROCERY_SMOKE_EDITOR_PASSWORD };
const VIEWER = { username: process.env.GROCERY_SMOKE_VIEWER, password: process.env.GROCERY_SMOKE_VIEWER_PASSWORD };
const SLUG = process.env.GROCERY_SMOKE_SPACE_SLUG;
const STATE = process.env.GROCERY_SMOKE_STATE;
if (
  ![OWNER, EDITOR, VIEWER].every((u) => /^e2e-/.test(u.username ?? '') && u.password) ||
  !/^e2e-grocery-/.test(SLUG ?? '') ||
  !STATE
) {
  throw new Error('GROCERY_SMOKE_USER / _EDITOR / _VIEWER (e2e-*), their passwords, GROCERY_SMOKE_SPACE_SLUG (e2e-grocery-*) and GROCERY_SMOKE_STATE are required');
}
const UI_TIMEOUT = 30_000;
const APP_FRAME = 'iframe[title="app"]';

const ok = (what) => console.log(`ok   ${what}`);
function check(cond, what, detail) {
  if (!cond) throw new Error(`FAIL: ${what}${detail === undefined ? '' : `: ${JSON.stringify(detail)}`}`);
  ok(what);
}

/** REST as the context's signed-in user (the page's own cookie). */
function api(context) {
  return async (method, url, { json, raw, timeout = 30_000, allowStatus } = {}) => {
    const r = await context.request.fetch(`${BASE}${url}`, {
      method,
      timeout,
      ...(raw !== undefined ? { data: raw, headers: { 'Content-Type': 'application/octet-stream' } } : json !== undefined ? { data: json } : {}),
    });
    const text = await r.text();
    if (allowStatus !== undefined && r.status() === allowStatus) return { status: r.status(), body: text ? JSON.parse(text) : null };
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

/** Upload the app to `<root>/Apps/grocery-list`, register, install in `space`, build. */
async function installApp(call, space) {
  const root = space.kind === 'personal' ? '/personal' : `/spaces/${space.slug}`;
  const dir = `${root}/Apps/${APP_SLUG}`;
  for (const rel of fixtureFiles(APP)) {
    await call('PUT', `/api/platform/files/content?path=${encodeURIComponent(`${dir}/${rel}`)}`, { raw: fs.readFileSync(path.join(APP, rel)) });
  }
  const { app } = await call('POST', '/api/platform/apps', { json: { source_path: dir } });
  fs.appendFileSync(STATE, `${app.id}\n`);
  const instance = await call('POST', `/api/platform/spaces/${space.id}/instances`, { json: { app_id: app.id } });
  const built = await call('POST', `/api/platform/apps/${app.id}/build`, { json: {}, timeout: 180_000 });
  check(built.ok && built.diagnostics.length === 0, `built in ${space.name} with zero diagnostics (${built.build?.bundle_bytes} B)`, built.diagnostics);
  const rows = async (sql = 'SELECT name, quantity, checked FROM items ORDER BY id') =>
    (await call('POST', `/api/platform/apps/instances/${instance.id}/rpc`, { json: { op: 'getAll', sql } })).rows;
  return { app, instance, rows };
}

const ui = (page) => page.frameLocator(APP_FRAME);

/** Apps tab -> the space's section -> Grocery list -> its runner, rendered. */
async function openFromAppsTab(page, spaceSlug) {
  const section = await openSpacePage(page, spaceSlug);
  await section.getByTestId(`apps-open-${APP_SLUG}`).click();
  await page.getByTestId('app-runner').waitFor({ timeout: UI_TIMEOUT });
  await ui(page).getByTestId('summary').waitFor({ timeout: UI_TIMEOUT });
  return section;
}

async function waitForFrameText(page, testID, expected, what) {
  const deadline = Date.now() + UI_TIMEOUT;
  let last = null;
  while (Date.now() < deadline) {
    last = await ui(page).getByTestId(testID).innerText({ timeout: 2000 }).catch(() => null);
    if (last === expected) return ok(what);
    await page.waitForTimeout(150);
  }
  throw new Error(`FAIL: ${what} (${testID} is ${JSON.stringify(last)}, expected ${JSON.stringify(expected)})`);
}

async function addItem(page, name) {
  const input = ui(page).getByTestId('new-item-name');
  await input.fill(name);
  await ui(page).getByTestId('add-item').click();
  const deadline = Date.now() + UI_TIMEOUT;
  while ((await input.inputValue()) !== '') {
    if (Date.now() > deadline) throw new Error(`FAIL: adding ${JSON.stringify(name)} was not acknowledged`);
    await page.waitForTimeout(100);
  }
}

const checkbox = (page, name) => ui(page).getByRole('checkbox', { name, exact: true });
async function waitForChecked(page, name, checked, what) {
  const box = checkbox(page, name);
  await box.waitFor({ timeout: UI_TIMEOUT });
  const deadline = Date.now() + UI_TIMEOUT;
  while ((await box.getAttribute('aria-checked')) !== String(checked)) {
    if (Date.now() > deadline) throw new Error(`FAIL: ${what}`);
    await page.waitForTimeout(150);
  }
  ok(what);
}

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

  // --- setup over REST: shared space + members, the app in both spaces ------
  const personal = (await owner.call('GET', '/api/platform/spaces')).spaces.find((s) => s.kind === 'personal');
  const shared = await owner.call('POST', '/api/platform/spaces', { json: { slug: SLUG, name: `E2E Grocery ${process.pid}` } });
  const directory = (await owner.call('GET', '/api/platform/users/directory')).users;
  for (const [user, role] of [
    [EDITOR, 'editor'],
    [VIEWER, 'viewer'],
  ]) {
    const { id } = directory.find((u) => u.username === user.username);
    await owner.call('POST', `/api/platform/spaces/${shared.id}/members`, { json: { user_id: id, role } });
  }
  const mine = await installApp(owner.call, personal);
  const ours = await installApp(owner.call, shared);
  ok(`installed in ${personal.name} (instance ${mine.instance.id}) and ${SLUG} (instance ${ours.instance.id}; editor + viewer)`);

  // --- 1. personal space: add, check, detail, clear, reload ---------------
  const { page } = owner;
  const section = await openFromAppsTab(page, personal.slug);
  check((await section.innerText()).includes('Grocery list'), 'the Apps tab lists Grocery list under the personal space');
  await waitForFrameText(page, 'summary', '0 to buy, 0 checked', 'the new list is empty');

  await addItem(page, 'Milk');
  await addItem(page, 'Eggs');
  await ui(page).getByTestId('new-item-name').fill('Bread');
  await ui(page).getByTestId('new-item-name').press('Enter');
  await waitForFrameText(page, 'summary', '3 to buy, 0 checked', 'three items added (button and Enter)');
  await addItem(page, '  milk ');
  await waitForFrameText(page, 'summary', '3 to buy, 0 checked', 'adding "milk" again does not duplicate it (addItem action)');
  check(JSON.stringify((await mine.rows()).map((r) => r.name)) === '["Milk","Eggs","Bread"]', 'the items are in the instance database', await mine.rows());

  await checkbox(page, 'Milk').click();
  await checkbox(page, 'Eggs').click();
  await waitForFrameText(page, 'summary', '1 to buy, 2 checked', 'checked Milk and Eggs');
  await waitForChecked(page, 'Milk', true, 'Milk shows as checked');
  await checkbox(page, 'Eggs').click();
  await waitForFrameText(page, 'summary', '2 to buy, 1 checked', 'unchecked Eggs');
  await waitForChecked(page, 'Eggs', false, 'Eggs shows as unchecked');
  check(
    JSON.stringify((await mine.rows()).map((r) => [r.name, r.checked])) === '[["Milk",1],["Eggs",0],["Bread",0]]',
    'checked state is in the database',
    await mine.rows(),
  );

  const breadId = (await mine.rows('SELECT id FROM items WHERE name = \'Bread\''))[0].id;
  await ui(page).getByTestId(`item-${breadId}`).click();
  await ui(page).getByTestId('detail-status').waitFor({ timeout: UI_TIMEOUT });
  check((await ui(page).getByTestId('detail-name').inputValue()) === 'Bread', "the detail screen shows the item's name");
  await waitForFrameText(page, 'detail-status', 'Not checked', 'the detail screen shows its status');
  await ui(page).getByTestId('detail-quantity').fill('2 loaves');
  await ui(page).getByTestId('detail-save').click();
  await ui(page).getByTestId('summary').waitFor({ timeout: UI_TIMEOUT });
  await ui(page).getByText('× 2 loaves').waitFor({ timeout: UI_TIMEOUT });
  ok('saving the detail screen goes back to the list, which shows the quantity');
  check((await mine.rows()).find((r) => r.name === 'Bread')?.quantity === '2 loaves', 'the quantity is in the database');

  await ui(page).getByTestId('clear-checked').click();
  await waitForFrameText(page, 'message', 'Cleared 1 checked item', 'Clear checked reports one item cleared (clearChecked action)');
  await waitForFrameText(page, 'summary', '2 to buy, 0 checked', 'the checked item is gone from the list');
  check(JSON.stringify((await mine.rows()).map((r) => r.name)) === '["Eggs","Bread"]', 'only the checked item was deleted', await mine.rows());

  await page.reload();
  await ui(page).getByTestId('summary').waitFor({ timeout: UI_TIMEOUT });
  await waitForFrameText(page, 'summary', '2 to buy, 0 checked', 'after a page reload the list is the same');
  check((await ui(page).getByText('Eggs').count()) === 1 && (await ui(page).getByText('× 2 loaves').count()) === 1, 'after a reload Eggs and Bread (× 2 loaves) are shown');

  // --- 2. shared space: a second member sees changes live ------------------
  await page.goto(`${BASE}/`);
  await openFromAppsTab(page, SLUG);
  const editor = await signIn(browser, EDITOR);
  watchSandboxRequests(editor.page, fromSandbox);
  const editorSection = await openFromAppsTab(editor.page, SLUG);
  check(!(await editorSection.innerText()).includes('View only'), 'the editor can edit the shared list');
  await waitForFrameText(editor.page, 'summary', '0 to buy, 0 checked', "the editor sees the shared list (not the owner's personal one)");

  await addItem(page, 'Apples');
  await ui(editor.page).getByText('Apples', { exact: true }).waitFor({ timeout: UI_TIMEOUT });
  await waitForFrameText(editor.page, 'summary', '1 to buy, 0 checked', "the owner's new item shows up for the editor without a reload (db_changed)");
  await checkbox(editor.page, 'Apples').click();
  await waitForChecked(page, 'Apples', true, "the editor's check shows up for the owner without a reload");
  await addItem(editor.page, 'Coffee');
  await waitForFrameText(page, 'summary', '1 to buy, 1 checked', "the editor's new item shows up for the owner");
  check(JSON.stringify((await ours.rows()).map((r) => [r.name, r.checked])) === '[["Apples",1],["Coffee",0]]', 'the shared database has both members\' changes', await ours.rows());
  check((await mine.rows()).length === 2, "the personal list is untouched by the shared space's", await mine.rows());

  // --- 3. the viewer: read-only ---------------------------------------------
  const viewer = await signIn(browser, VIEWER);
  watchSandboxRequests(viewer.page, fromSandbox);
  const viewerSection = await openFromAppsTab(viewer.page, SLUG);
  check((await viewerSection.innerText()).includes('View only'), 'the Apps tab marks the app "View only" for the viewer');
  await viewer.page.getByTestId('app-runner-read-only').waitFor({ timeout: UI_TIMEOUT });
  await waitForFrameText(viewer.page, 'summary', '1 to buy, 1 checked', 'the viewer sees the shared list');
  await ui(viewer.page).getByTestId('view-only').waitFor({ timeout: UI_TIMEOUT });
  check(
    (await ui(viewer.page).getByTestId('new-item-name').count()) === 0 && (await ui(viewer.page).getByTestId('clear-checked').count()) === 0,
    'the app hides the add and clear controls from the viewer',
  );
  await checkbox(viewer.page, 'Coffee').click({ force: true });
  await viewer.page.waitForTimeout(1000);
  await waitForChecked(viewer.page, 'Coffee', false, "the viewer's checkbox is disabled");

  const rpc = `/api/platform/apps/instances/${ours.instance.id}/rpc`;
  const write = await viewer.call('POST', rpc, { json: { op: 'run', sql: 'DELETE FROM items', params: [] }, allowStatus: 403 });
  check(write.status === 403, 'a direct write as the viewer is refused (403)', write);
  const action = await viewer.call('POST', rpc, { json: { op: 'action', name: 'clearChecked', params: {} }, allowStatus: 403 });
  check(action.status === 403, 'running the clearChecked action as the viewer is refused (403)', action);
  check((await ours.rows()).length === 2, 'the shared database is unchanged by the viewer', await ours.rows());

  check(fromSandbox.length === 0, 'no request from a sandbox frame reached anything', fromSandbox);
  await viewer.context.close();
  await editor.context.close();
  await owner.context.close();
  console.log('PASS: grocery app smoke');
} finally {
  await browser.close();
}
