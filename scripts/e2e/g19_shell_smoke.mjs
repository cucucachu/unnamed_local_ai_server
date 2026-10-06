// M19-06 GATE G19: the redesigned app shell (see g19_shell_smoke.sh).
//
// Env (from g19_shell_smoke.sh): G19_USER / G19_PASSWORD (e2e-g19-*),
// G19_{NEEDS,UNREAD,PLAIN}_{TITLE,ID} (the seeded chats), G19_SPACE_SLUG
// (e2e-g19-*), G19_STATE (a file this writes the app id to, for cleanup),
// E2E_BASE (default http://localhost).
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

import { FIXTURE, fixtureFiles } from './app_fixture.mjs';
import { loginThroughUi } from './auth_helpers.mjs';
import { currentThreadId } from './nav_helpers.mjs';

const BASE = (process.env.E2E_BASE ?? 'http://localhost').replace(/\/$/, '');
const env = (name) => {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required (run g19_shell_smoke.sh)`);
  return value;
};
const USER = { username: env('G19_USER'), password: env('G19_PASSWORD') };
const SLUG = env('G19_SPACE_SLUG');
const STATE = env('G19_STATE');
if (!/^e2e-g19-/.test(USER.username) || !/^e2e-g19-/.test(SLUG)) throw new Error('e2e-g19-* user and space only');
const CHATS = {
  needs: { title: env('G19_NEEDS_TITLE'), id: env('G19_NEEDS_ID') },
  unread: { title: env('G19_UNREAD_TITLE'), id: env('G19_UNREAD_ID') },
  plain: { title: env('G19_PLAIN_TITLE'), id: env('G19_PLAIN_ID') },
};
const TIMEOUT = 30_000;
const SPACE_NAME = `E2E G19 ${process.pid}`;

const ok = (what) => console.log(`ok   ${what}`);
function check(cond, what, detail) {
  if (!cond) throw new Error(`FAIL: ${what}${detail === undefined ? '' : `: ${JSON.stringify(detail)}`}`);
  ok(what);
}

const browser = await chromium.launch();
try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  const call = async (method, url, options = {}) => {
    const r = await context.request.fetch(`${BASE}${url}`, { method, ...options });
    const text = await r.text();
    if (!r.ok()) throw new Error(`${method} ${url}: ${r.status()} ${text}`);
    return text ? JSON.parse(text) : null;
  };

  // --- 1. a cold launch lands in an empty chat ----------------------------
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
  await loginThroughUi(page, USER);
  await page.locator('[data-testid="chat-view"][data-thread-id=""]').waitFor({ state: 'attached', timeout: TIMEOUT });
  check(new URL(page.url()).pathname.startsWith('/chat'), 'sign-in lands in Chat', page.url());
  ok('the chat on screen is a new, empty one, not the newest seeded chat');

  // --- 2. the drawer lists chats in "needs you" order ---------------------
  await page.getByTestId('chat-history-button').click();
  const drawer = page.getByTestId('chat-drawer');
  await drawer.waitFor({ state: 'visible', timeout: TIMEOUT });
  await drawer.getByTestId('thread-row').filter({ hasText: CHATS.plain.title }).waitFor({ timeout: TIMEOUT });
  const rows = await drawer.getByTestId('thread-row').allInnerTexts();
  const at = (chat) => rows.findIndex((text) => text.includes(chat.title));
  check(
    at(CHATS.needs) === 0 && at(CHATS.unread) === 1 && at(CHATS.plain) === 2,
    'the drawer lists the chat waiting on you, then the unread one, then the newest plain one',
    rows,
  );
  check((await drawer.getByTestId(`thread-needs-approval-${CHATS.needs.id}`).count()) === 1, 'the waiting chat is marked');
  check((await drawer.getByTestId(`thread-unread-${CHATS.unread.id}`).count()) === 1, 'the unread chat is marked');
  await drawer.getByTestId('thread-row').filter({ hasText: CHATS.plain.title }).click();
  await drawer.waitFor({ state: 'hidden', timeout: TIMEOUT });
  check((await currentThreadId(page)) === CHATS.plain.id, 'a chat opens from the drawer');

  // --- 3. + makes a new chat ----------------------------------------------
  await page.getByTestId('new-chat-header-button').click();
  await page.locator('[data-testid="chat-view"][data-thread-id=""]').waitFor({ state: 'attached', timeout: TIMEOUT });
  ok('+ makes a new, empty chat');

  // --- 4. Apps shows the grid; swiping reaches a shared space -------------
  const shared = await call('POST', '/api/platform/spaces', { data: { slug: SLUG, name: SPACE_NAME } });
  const appDir = `/spaces/${SLUG}/Apps/runtime-check`;
  for (const rel of fixtureFiles()) {
    await call('PUT', `/api/platform/files/content?path=${encodeURIComponent(`${appDir}/${rel}`)}`, {
      data: fs.readFileSync(path.join(FIXTURE, rel)),
      headers: { 'Content-Type': 'application/octet-stream' },
    });
  }
  const { app } = await call('POST', '/api/platform/apps', { data: { source_path: appDir } });
  fs.writeFileSync(STATE, `${app.id}\n`);
  await call('POST', `/api/platform/spaces/${shared.id}/instances`, { data: { app_id: app.id } });
  ok(`shared space ${SLUG} with runtime-check installed`);

  await page.getByRole('tab', { name: 'Apps' }).click();
  await page.getByTestId('home-launcher').waitFor({ timeout: TIMEOUT });
  const dock = page.getByTestId('home-system');
  for (const id of ['home-open-files', 'home-open-routines', 'home-open-settings', 'apps-catalog']) {
    check(await dock.getByTestId(id).isVisible(), `the dock has ${id}`);
  }
  const sharedDot = page.getByTestId(`home-space-${SLUG}`);
  await sharedDot.waitFor({ timeout: TIMEOUT });
  const firstPage = page.locator('[data-testid^="apps-space-"]').first();
  check((await firstPage.getAttribute('data-testid')) !== `apps-space-${SLUG}`, 'Personal is the first page');
  check(await firstPage.getByTestId('apps-empty').isVisible(), 'the Personal page is empty');

  await page.getByTestId('home-pages').hover();
  await page.mouse.wheel(1200, 0);
  await page.waitForFunction((slug) => document.querySelector(`[data-testid="home-space-${slug}"]`)?.getAttribute('aria-selected') === 'true', SLUG, { timeout: TIMEOUT });
  await page.waitForFunction(() => {
    const pager = document.querySelector('[data-testid="home-pages"]');
    return pager && pager.scrollLeft > 0 && pager.scrollLeft % pager.clientWidth < 2;
  }, null, { timeout: TIMEOUT });
  const tile = page.getByTestId(`apps-space-${SLUG}`).getByTestId('apps-open-runtime-check');
  const [box, pager] = [await tile.boundingBox(), await page.getByTestId('home-pages').boundingBox()];
  check(box !== null && box.x >= pager.x && box.x + box.width <= pager.x + pager.width, 'a swipe reaches the shared space, with its app on screen');
  const hasIcon = (await tile.getByTestId('app-icon-glyph-runtime-check').count()) + (await tile.getByTestId('app-icon-letter-runtime-check').count());
  check(hasIcon === 1, 'the app is an icon tile');
  check(await page.getByRole('heading', { name: SPACE_NAME }).first().isVisible(), "the title is the space's name");

  // --- 5. Files, Routines and Settings open from their tiles -------------
  const backToApps = async (button) => {
    await button.click();
    await page.waitForURL((url) => url.pathname === '/apps', { timeout: TIMEOUT });
    await page.getByTestId('home-launcher').waitFor({ timeout: TIMEOUT });
  };
  await page.getByTestId('home-open-files').click();
  await page.waitForURL((url) => url.pathname === '/files', { timeout: TIMEOUT });
  check(new URL(page.url()).searchParams.get('path') === `/spaces/${SLUG}`, "Files opens at the page's space", page.url());
  await page.getByTestId('files-screen').getByText('Apps', { exact: true }).first().waitFor({ timeout: TIMEOUT });
  await backToApps(page.getByTestId('back-to-apps'));
  check((await sharedDot.getAttribute('aria-selected')) === 'true', 'back from Files returns to the same page');

  await page.getByTestId('home-open-routines').click();
  await page.getByTestId('routines-screen').waitFor({ timeout: TIMEOUT });
  ok('the Routines tile opens Routines');
  await backToApps(page.getByTestId('routines-screen').getByTestId('settings-back-button'));

  await page.getByTestId('home-open-settings').click();
  await page.getByTestId('settings-account').waitFor({ timeout: TIMEOUT });
  check((await page.getByTestId('settings-account').innerText()).includes(USER.username), 'the Settings tile opens Settings');
  await backToApps(page.getByTestId('settings-screen').getByTestId('settings-back-button'));
  ok('back from Routines and Settings returns to Apps');

  console.log('PASS: G19 app shell smoke');
} finally {
  await browser.close();
}
