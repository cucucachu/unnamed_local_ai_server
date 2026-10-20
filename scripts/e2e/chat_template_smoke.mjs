// #467: the Group chat template in the browser. Driven by chat_template_smoke.sh
// (which makes the users and cleans up); see its header for what is checked.
//
// Env: CHAT_SMOKE_USER / _EDITOR / _VIEWER and their _PASSWORDs (e2e-*
// users), CHAT_SMOKE_SPACE_SLUG (e2e-chat-*), CHAT_SMOKE_STATE (a file this
// writes the app id to, for the cleanup trap), CHAT_SMOKE_BASE_URL (default
// http://localhost/), CHAT_SMOKE_SCREENSHOTS (optional: a folder for the owner's and
// the editor's view of the room).
import fs from 'node:fs';
import { chromium } from 'playwright';

import { loginThroughUi } from './auth_helpers.mjs';

const BASE = (process.env.CHAT_SMOKE_BASE_URL ?? 'http://localhost/').replace(/\/$/, '');
const OWNER = { username: process.env.CHAT_SMOKE_USER, password: process.env.CHAT_SMOKE_PASSWORD };
const EDITOR = { username: process.env.CHAT_SMOKE_EDITOR, password: process.env.CHAT_SMOKE_EDITOR_PASSWORD };
const VIEWER = { username: process.env.CHAT_SMOKE_VIEWER, password: process.env.CHAT_SMOKE_VIEWER_PASSWORD };
const SLUG = process.env.CHAT_SMOKE_SPACE_SLUG;
const STATE = process.env.CHAT_SMOKE_STATE;
if (![OWNER, EDITOR, VIEWER].every((u) => /^e2e-/.test(u.username ?? '') && u.password) || !/^e2e-chat-/.test(SLUG ?? '') || !STATE) {
  throw new Error('CHAT_SMOKE_USER / _EDITOR / _VIEWER (e2e-*), their passwords, CHAT_SMOKE_SPACE_SLUG (e2e-chat-*) and CHAT_SMOKE_STATE are required');
}
const UI_TIMEOUT = 30_000;
// Another member's message arrives through the events socket and a re-run query.
const LIVE_TIMEOUT = 10_000;
const SEARCH_TIMEOUT = 120_000;
const BUILD_TIMEOUT = 240_000;
const APP_FRAME = 'iframe[title="app"]';

const ok = (what) => console.log(`ok   ${what}`);
function check(cond, what, detail) {
  if (!cond) throw new Error(`FAIL: ${what}${detail === undefined ? '' : `: ${JSON.stringify(detail)}`}`);
  ok(what);
}

/** Polls `probe` (which returns [done, detail]) until done; returns how long it took. */
async function until(what, probe, timeout = UI_TIMEOUT) {
  const started = Date.now();
  let detail;
  while (Date.now() - started < timeout) {
    let done;
    try {
      [done, detail] = await probe();
    } catch (e) {
      [done, detail] = [false, e.message];
    }
    if (done) {
      const ms = Date.now() - started;
      ok(`${what} (${ms} ms)`);
      return ms;
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`FAIL: ${what}: ${JSON.stringify(detail)}`);
}

function api(context) {
  return async (method, url, { json, timeout = 30_000, allow = [] } = {}) => {
    const r = await context.request.fetch(`${BASE}${url}`, { method, timeout, ...(json !== undefined ? { data: json } : {}) });
    const text = await r.text();
    if (!r.ok() && !allow.includes(r.status())) throw new Error(`${method} ${url}: ${r.status()} ${text}`);
    return { status: r.status(), body: text ? JSON.parse(text) : null };
  };
}

async function signIn(browser, user) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${BASE}/`);
  await loginThroughUi(page, user);
  const call = api(context);
  return { context, page, call, json: async (...args) => (await call(...args)).body };
}

const ui = (page) => page.frameLocator(APP_FRAME);
const text = (page, testID) => ui(page).getByTestId(testID).innerText({ timeout: 2000 });
const count = (page, testID) => ui(page).getByTestId(testID).count();

async function openApp(page, instance, readyTestID) {
  await page.goto(`${BASE}/apps/${instance.id}`);
  await page.getByTestId('app-runner').waitFor({ timeout: UI_TIMEOUT });
  await ui(page).getByTestId(readyTestID).waitFor({ timeout: UI_TIMEOUT });
}

async function post(page, body) {
  await ui(page).getByTestId('composer').fill(body);
  await ui(page).getByTestId('send').click();
}

const browser = await chromium.launch();
try {
  const owner = await signIn(browser, OWNER);
  ok(`signed in as ${OWNER.username}`);

  // --- setup over REST: shared space, an editor and a viewer, Group chat--------
  const shared = await owner.json('POST', '/api/platform/spaces', { json: { slug: SLUG, name: `E2E Chat ${process.pid}` } });
  const directory = (await owner.json('GET', '/api/platform/users/directory')).users;
  const user = (u) => directory.find((d) => d.username === u.username);
  await owner.json('POST', `/api/platform/spaces/${shared.id}/members`, { json: { user_id: user(EDITOR).id, role: 'editor' } });
  await owner.json('POST', `/api/platform/spaces/${shared.id}/members`, { json: { user_id: user(VIEWER).id, role: 'viewer' } });
  const templates = (await owner.json('GET', '/api/platform/app-templates')).templates;
  check(templates.some((t) => t.slug === 'group-chat'), 'the catalog lists Group chat');
  const out = await owner.json('POST', `/api/platform/spaces/${shared.id}/app-templates/group-chat/install`, { json: {}, timeout: BUILD_TIMEOUT });
  fs.appendFileSync(STATE, `${out.app.id}\n`);
  check(out.build?.ok && out.build.diagnostics.length === 0, 'installed and built Group chat with zero diagnostics', out.build);
  const instance = out.instance;
  const rpc = (who, body) => who.call('POST', `/api/platform/apps/instances/${instance.id}/rpc`, { json: body, allow: [403] });
  const messages = async () =>
    (await rpc(owner, { op: 'getAll', sql: 'SELECT id, body, edited, deleted, _created_by FROM messages ORDER BY id', params: [] })).body.rows;

  const editor = await signIn(browser, EDITOR);
  const viewer = await signIn(browser, VIEWER);
  ok('signed in as the editor and the viewer');
  await openApp(owner.page, instance, 'chat-empty');
  await openApp(editor.page, instance, 'chat-empty');
  ok('Group chat opens empty for both');

  // --- live, both ways ---------------------------------------------------------
  await post(owner.page, 'Dinner at ours on Saturday?');
  await until('the owner’s message is saved, as the owner', async () => {
    const ms = await messages();
    return [ms.length === 1 && ms[0]._created_by === user(OWNER).id, ms];
  });
  const [first] = await messages();
  const live1 = await until('the editor sees it arrive without reloading', async () => [(await text(editor.page, `message-${first.id}-body`)) === 'Dinner at ours on Saturday?', null], LIVE_TIMEOUT);
  check((await text(editor.page, `message-${first.id}-sender`)).length > 0, 'with the owner’s name above it');
  check((await text(editor.page, `message-${first.id}-day`)) === 'Today', 'under a "Today" separator');
  check((await count(owner.page, `message-${first.id}-sender`)) === 0, 'the owner’s own message has no name header');

  await post(editor.page, 'Yes! I can bring dessert');
  await until('the editor’s reply is saved', async () => [(await messages()).length === 2, await messages()]);
  const reply = (await messages())[1];
  const live2 = await until('the owner sees the reply arrive without reloading', async () => [(await text(owner.page, `message-${reply.id}-body`)) === 'Yes! I can bring dessert', null], LIVE_TIMEOUT);
  console.log(`     cross-member refresh: ${live1} ms and ${live2} ms`);

  // --- edit and delete your own ------------------------------------------------
  await ui(owner.page).getByTestId(`message-${reply.id}-bubble`).click({ force: true });
  check((await count(owner.page, 'message-actions')) === 0, 'tapping someone else’s message offers no edit or delete');
  await ui(owner.page).getByTestId(`message-${first.id}-bubble`).click();
  await ui(owner.page).getByTestId('message-edit').click();
  await ui(owner.page).getByTestId('editing-banner').waitFor({ timeout: UI_TIMEOUT });
  check((await ui(owner.page).getByTestId('composer').inputValue()) === 'Dinner at ours on Saturday?', 'Edit puts the message in the box');
  await ui(owner.page).getByTestId('composer').fill('Dinner at ours on Saturday at 7?');
  await ui(owner.page).getByTestId('send').click();
  await until('editing saves it and marks it edited', async () => {
    const m = (await messages()).find((x) => x.id === first.id);
    return [m.body === 'Dinner at ours on Saturday at 7?' && m.edited === 1, m];
  });
  await until('the editor sees the edit, marked "edited"', async () => {
    const [body, meta] = [await text(editor.page, `message-${first.id}-body`), await text(editor.page, `message-${first.id}-meta`)];
    return [body === 'Dinner at ours on Saturday at 7?' && meta.includes('edited') && !meta.includes(' by '), { body, meta }];
  }, LIVE_TIMEOUT);

  await post(owner.page, 'oops, wrong chat');
  await until('a third message', async () => [(await messages()).length === 3, await messages()]);
  const oops = (await messages())[2];
  await ui(owner.page).getByTestId(`message-${oops.id}-bubble`).click();
  await ui(owner.page).getByTestId('message-delete').click();
  await until('deleting clears it and marks it deleted', async () => {
    const m = (await messages()).find((x) => x.id === oops.id);
    return [m.deleted === 1 && m.body === '', m];
  });
  await until('the editor sees "Message deleted"', async () => [(await text(editor.page, `message-${oops.id}-body`)) === 'Message deleted', await text(editor.page, `message-${oops.id}-body`)], LIVE_TIMEOUT);

  if (process.env.CHAT_SMOKE_SCREENSHOTS) {
    await owner.page.screenshot({ path: `${process.env.CHAT_SMOKE_SCREENSHOTS}/chat-owner.png` });
    await editor.page.screenshot({ path: `${process.env.CHAT_SMOKE_SCREENSHOTS}/chat-editor.png` });
  }

  // --- the viewer reads only ---------------------------------------------------
  await openApp(viewer.page, instance, `message-${first.id}`);
  check((await text(viewer.page, `message-${reply.id}-body`)) === 'Yes! I can bring dessert', 'the viewer reads the room');
  check((await count(viewer.page, 'composer')) === 0 && (await text(viewer.page, 'view-only')).startsWith('View only'), 'the viewer has no message box, only "View only"');
  await ui(viewer.page).getByTestId(`message-${first.id}-bubble`).click({ force: true });
  check((await count(viewer.page, 'message-actions')) === 0, 'the viewer can’t select messages');
  const refused = await rpc(viewer, { op: 'run', sql: "INSERT INTO messages (body) VALUES ('sneaky')", params: [] });
  check(refused.status === 403 && (await messages()).length === 3, 'a write as the viewer is refused', refused);

  // --- search ------------------------------------------------------------------
  await ui(owner.page).getByTestId('open-search').click();
  await ui(owner.page).getByTestId('search').waitFor({ timeout: UI_TIMEOUT });
  let typed = 0;
  await until('searching "dessert" finds the editor’s reply', async () => {
    if (Date.now() - typed > 5000) {
      await ui(owner.page).getByTestId('search').fill('');
      await ui(owner.page).getByTestId('search').fill('dessert');
      typed = Date.now();
    }
    return [(await count(owner.page, `result-${reply.id}`)) === 1, null];
  }, SEARCH_TIMEOUT);

  await viewer.context.close();
  await editor.context.close();
  await owner.context.close();
  console.log('PASS: chat template smoke');
} finally {
  await browser.close();
}
