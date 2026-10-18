// M22-10: the Files text viewer/editor on the live stack, invoked by
// `m22_text_editor_smoke.sh`, not run directly.
//
// As a throwaway `e2e-m22t-*` user, through the real web UI:
//   1. Seeds /personal/notes/<plan>.md (with a relative link) and list.txt.
//   2. Tapping the markdown file in Files opens the viewer, rendered as
//      markdown; its relative link opens list.txt's folder in Files.
//   3. Edit, type a change, and before saving the real agent edits the same
//      file from a chat in a second tab (edit_file allowed for this user;
//      one retry if the model didn't call it).
//   4. Save shows the conflict prompt; "Reload theirs" shows the agent's
//      change; editing again and saving writes on top of it ("Saved.").
// Checks the file's bytes over the files API, never the reply text alone.

import { chromium } from 'playwright';

import { createE2eUser, deleteE2eUsers, loginThroughUi, sessionCookie } from './auth_helpers.mjs';
import { openSpace, uploadFile } from './files_helpers.mjs';
import { openSystemApp, startNewChat } from './nav_helpers.mjs';

const BASE_URL = process.env.TEXT_SMOKE_BASE_URL ?? 'http://localhost/';
const API = new URL('/api/platform', BASE_URL).toString();
const UI_TIMEOUT_MS = 20_000;
const AGENT_TIMEOUT_MS = 240_000;

const PLAN = `plan-${process.pid}.md`;
const PLAN_PATH = `/personal/notes/${PLAN}`;
const SEED = '# Weekend plan\n\n- [ ] buy milk\n- [ ] fix the gate\n\nSee [the list](list.txt).\n';

async function api(cookie, method, path, json) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: { Cookie: cookie, ...(json ? { 'Content-Type': 'application/json' } : {}) },
    body: json ? JSON.stringify(json) : undefined,
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`${method} ${path}: HTTP ${response.status} ${text}`);
  return text;
}

const readPlan = (cookie) => api(cookie, 'GET', `/files/stream?path=${encodeURIComponent(PLAN_PATH)}`);

async function openPlan(page) {
  await page.getByTestId('files-screen').getByText(PLAN, { exact: true }).filter({ visible: true }).first().click();
  await page.getByTestId('file-view').waitFor({ state: 'visible', timeout: UI_TIMEOUT_MS });
}

/** One chat turn asking the agent to tick "buy milk"; true once the file shows it. */
async function agentTicksMilk(page, cookie) {
  await startNewChat(page);
  const input = page.getByPlaceholder('Message…');
  await input.waitFor({ state: 'visible', timeout: UI_TIMEOUT_MS });
  await input.fill(
    `In the file ${PLAN_PATH}, use edit_file to replace "- [ ] buy milk" with "- [x] buy milk". Change nothing else.`,
  );
  await page.getByRole('button', { name: 'Send message' }).click();
  await page.getByRole('button', { name: 'Stop generating' }).waitFor({ timeout: UI_TIMEOUT_MS }).catch(() => {});
  const deadline = Date.now() + AGENT_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if ((await readPlan(cookie)).includes('- [x] buy milk')) return true;
    if ((await page.getByRole('button', { name: 'Stop generating' }).count()) === 0) break;
    await page.waitForTimeout(1000);
  }
  return (await readPlan(cookie)).includes('- [x] buy milk');
}

async function main() {
  const startedAt = Date.now();
  const user = createE2eUser({ prefix: 'e2e-m22t' });
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    page.on('dialog', (dialog) => dialog.accept());
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
    await loginThroughUi(page, user);
    const cookie = await sessionCookie(context);

    // --- 1. seed -----------------------------------------------------------
    await uploadFile(cookie, '/personal/notes', PLAN, Buffer.from(SEED), 'text/markdown').catch(async () => {
      await api(cookie, 'POST', '/files/mkdir', { path: '/personal/notes' });
      await uploadFile(cookie, '/personal/notes', PLAN, Buffer.from(SEED), 'text/markdown');
    });
    await uploadFile(cookie, '/personal/notes', 'list.txt', Buffer.from('milk\nnails\n'), 'text/plain');
    await api(cookie, 'PATCH', '/tools', { changes: [{ name: 'edit_file', permission: 'allow' }] });
    console.log(`Step 1 OK: seeded ${PLAN_PATH}`);

    // --- 2. view -----------------------------------------------------------
    await openSystemApp(page, 'files');
    await openSpace(page, 'Personal');
    await page.getByTestId('files-screen').getByText('notes', { exact: true }).click();
    await openPlan(page);
    await page.getByTestId('markdown').getByText('Weekend plan').waitFor({ timeout: UI_TIMEOUT_MS });
    await page.getByTestId('file-link').click();
    await page.getByTestId('file-entry-highlighted').getByText('list.txt', { exact: true }).waitFor({ timeout: UI_TIMEOUT_MS });
    if (!page.url().includes(encodeURIComponent('/personal/notes/list.txt')) && !page.url().includes('/personal/notes/list.txt')) {
      throw new Error(`the relative link should open list.txt in Files, got ${page.url()}`);
    }
    console.log('Step 2 OK: the markdown renders and its relative link opens Files');

    // --- 3. edit, and the agent writes in between ---------------------------
    await openPlan(page);
    await page.getByTestId('file-edit').click();
    const editor = page.getByTestId('file-editor');
    await editor.waitFor({ state: 'visible', timeout: UI_TIMEOUT_MS });
    await editor.fill(`${SEED}- [ ] mine: call the plumber\n`);

    const chat = await context.newPage();
    await chat.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
    let ticked = false;
    for (const attempt of [1, 2]) {
      ticked = await agentTicksMilk(chat, cookie);
      console.log(`  agent attempt ${attempt}: ${ticked ? 'edited the file' : 'did not edit the file'}`);
      if (ticked) break;
    }
    if (!ticked) throw new Error('the agent never ticked "buy milk" in the file');
    await chat.close();
    console.log('Step 3 OK: the agent edited the file while it was open in the editor');

    // --- 4. conflict, reload, save -----------------------------------------
    await page.getByTestId('file-save').click();
    await page.getByTestId('file-conflict').waitFor({ state: 'visible', timeout: UI_TIMEOUT_MS });
    if (process.env.TEXT_SMOKE_SCREENSHOT) await page.screenshot({ path: process.env.TEXT_SMOKE_SCREENSHOT });
    if ((await readPlan(cookie)).includes('mine: call the plumber')) {
      throw new Error('the stale save overwrote the agent change');
    }
    await page.getByTestId('file-conflict-reload').click();
    await page.getByTestId('file-view').waitFor({ state: 'visible', timeout: UI_TIMEOUT_MS });
    await page.getByTestId('markdown').getByText('☑ buy milk').waitFor({ timeout: UI_TIMEOUT_MS });

    await page.getByTestId('file-edit').click();
    const current = await editor.inputValue();
    await editor.fill(`${current}- [ ] mine: call the plumber\n`);
    await page.getByTestId('file-save').click();
    await page.getByTestId('file-notice').getByText('Saved.').waitFor({ timeout: UI_TIMEOUT_MS });
    const final = await readPlan(cookie);
    if (!final.includes('- [x] buy milk') || !final.includes('- [ ] mine: call the plumber')) {
      throw new Error(`the saved file should have both changes: ${JSON.stringify(final)}`);
    }
    console.log('Step 4 OK: the stale save got the conflict prompt; reload + save kept both changes');

    console.log(`PASS: view, edit, conflict and save in ${Math.round((Date.now() - startedAt) / 1000)}s`);
  } finally {
    await browser.close();
    deleteE2eUsers(user.username);
  }
}

main().catch((error) => {
  console.error('FAIL:', error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
