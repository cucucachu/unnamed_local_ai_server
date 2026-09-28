// M13-05 GATE G13: the agent-built grocery list app in the real web app,
// through Caddy, in headless Chromium. Driven by g13_grocery_smoke.py
// (see g13_grocery_smoke.sh).
//
// Env: G13_USER / G13_PASSWORD (e2e-g13-*), G13_STATE (JSON: instance_id,
// slug, space_slug, ui_item, agent_item). G13_BASE_URL / E2E_BASE default
// http://localhost/.
import fs from 'node:fs';
import { chromium } from 'playwright';

import { loginThroughUi } from './auth_helpers.mjs';

const BASE = (process.env.G13_BASE_URL ?? process.env.E2E_BASE ?? 'http://localhost/').replace(/\/$/, '');
const USER = { username: process.env.G13_USER, password: process.env.G13_PASSWORD };
const STATE_PATH = process.env.G13_STATE;
if (!/^e2e-/.test(USER.username ?? '') || !USER.password || !STATE_PATH) {
  throw new Error('G13_USER (e2e-*), G13_PASSWORD and G13_STATE are required');
}

const UI_TIMEOUT = 60_000;
const APP_FRAME = 'iframe[title="app"]';

function readState() {
  return JSON.parse(fs.readFileSync(STATE_PATH, 'utf8'));
}

function writeState(update) {
  const state = readState();
  Object.assign(state, update);
  const tmp = `${STATE_PATH}.ui-tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(state, null, 2)}\n`);
  fs.renameSync(tmp, STATE_PATH);
}

const ok = (what) => console.log(`ok   ${what}`);

function api(context) {
  return async (method, url, { json, timeout = 30_000 } = {}) => {
    const r = await context.request.fetch(`${BASE}${url}`, {
      method,
      timeout,
      ...(json !== undefined ? { data: json } : {}),
    });
    const text = await r.text();
    if (!r.ok()) throw new Error(`${method} ${url}: ${r.status()} ${text}`);
    return text ? JSON.parse(text) : null;
  };
}

const ui = (page) => page.frameLocator(APP_FRAME);

async function openFromAppsTab(page, spaceSlug, slug) {
  await page.getByRole('tab', { name: 'Apps' }).click();
  const section = page.getByTestId(`apps-space-${spaceSlug}`);
  await section.waitFor({ timeout: UI_TIMEOUT });
  await section.getByTestId(`apps-open-${slug}`).click();
  await page.getByTestId('app-runner').waitFor({ timeout: UI_TIMEOUT });
  await page.locator(APP_FRAME).waitFor({ timeout: UI_TIMEOUT });
}

async function addItem(page, name) {
  const named = ui(page).getByTestId('new-item-name');
  if (await named.count()) {
    await named.fill(name);
    await ui(page).getByTestId('add-item').click();
    const deadline = Date.now() + UI_TIMEOUT;
    while ((await named.inputValue()) !== '') {
      if (Date.now() > deadline) throw new Error(`adding ${JSON.stringify(name)} was not acknowledged`);
      await page.waitForTimeout(100);
    }
    return;
  }
  const box = ui(page).getByRole('textbox').first();
  await box.waitFor({ timeout: UI_TIMEOUT });
  await box.fill(name);
  const add = ui(page).getByRole('button', { name: /^add$/i }).first();
  if (await add.count()) await add.click();
  else await box.press('Enter');
}

async function waitForText(page, name, what) {
  const deadline = Date.now() + UI_TIMEOUT;
  while (Date.now() < deadline) {
    const n = await ui(page).getByText(name, { exact: true }).count();
    if (n > 0) {
      ok(what);
      return;
    }
    await page.waitForTimeout(200);
  }
  throw new Error(`FAIL: ${what} (never saw ${JSON.stringify(name)} in the app frame)`);
}

async function waitAgentWritten(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const state = readState();
    if (state.agent_written) return state;
    if (state.ui_error) throw new Error(state.ui_error);
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error('timed out waiting for the agent to write a row');
}

const browser = await chromium.launch();
try {
  const state = readState();
  const { instance_id: instanceId, slug, space_slug: spaceSlug, ui_item: uiItem, agent_item: agentItem } = state;
  if (!instanceId || !slug || !spaceSlug || !uiItem || !agentItem) {
    throw new Error(`G13_STATE missing fields: ${JSON.stringify(state)}`);
  }

  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${BASE}/`);
  await loginThroughUi(page, USER);
  ok(`signed in as ${USER.username} through the login form`);

  await openFromAppsTab(page, spaceSlug, slug);
  if (new URL(page.url()).pathname !== `/apps/${instanceId}`) {
    throw new Error(`runner is ${page.url()}, expected /apps/${instanceId}`);
  }
  ok(`Apps tab opened ${slug} in the runner`);

  const iframe = page.locator(APP_FRAME);
  const sandboxAttr = await iframe.getAttribute('sandbox');
  if (sandboxAttr !== 'allow-scripts') {
    throw new Error(`sandbox is ${sandboxAttr}, expected allow-scripts`);
  }

  await addItem(page, uiItem);
  const call = api(context);
  const deadline = Date.now() + UI_TIMEOUT;
  let seen = false;
  while (Date.now() < deadline) {
    const body = await call('POST', `/api/platform/apps/instances/${instanceId}/rpc`, {
      json: { op: 'getAll', sql: "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'" },
    });
    for (const row of body.rows ?? []) {
      const rows = await call('POST', `/api/platform/apps/instances/${instanceId}/rpc`, {
        json: { op: 'getAll', sql: `SELECT * FROM ${row.name} LIMIT 50` },
      });
      if (JSON.stringify(rows.rows ?? []).includes(uiItem)) {
        seen = true;
        break;
      }
    }
    if (seen) break;
    await page.waitForTimeout(200);
  }
  if (!seen) throw new Error(`instance database does not contain ${uiItem} after the UI add`);
  await waitForText(page, uiItem, `the runner shows ${uiItem}`);
  writeState({ ui_written: true });
  ok(`UI wrote ${uiItem}; waiting for the agent's row`);

  await waitAgentWritten(180_000);
  try {
    await waitForText(page, agentItem, `the runner shows the agent's ${agentItem} (db_changed)`);
  } catch (first) {
    await page.reload();
    await page.locator(APP_FRAME).waitFor({ timeout: UI_TIMEOUT });
    await waitForText(page, agentItem, `after reload the runner shows ${agentItem} (${first.message})`);
  }
  await context.close();
} catch (err) {
  try {
    writeState({ ui_error: String(err?.message ?? err) });
  } catch {
    // state file may already be gone during cleanup
  }
  throw err;
} finally {
  await browser.close();
}
