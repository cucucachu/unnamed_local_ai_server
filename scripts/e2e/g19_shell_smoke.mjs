// M19-06 GATE G19: the redesigned app shell (see g19_shell_smoke.sh),
// M19-07's pager: Chat, Personal, then shared spaces, swiped through.
//
// Env (from g19_shell_smoke.sh): G19_USER / G19_PASSWORD (e2e-g19-*),
// G19_{NEEDS,UNREAD,PLAIN}_{TITLE,ID} and G19_OLDEST_TITLE (the seeded
// chats), G19_SPACE_SLUG
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
const OLDEST = env('G19_OLDEST_TITLE');
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
  const selected = (testId) =>
    page.waitForFunction((id) => document.querySelector(`[data-testid="${id}"]`)?.getAttribute('aria-selected') === 'true', testId, { timeout: TIMEOUT });
  const settled = () =>
    page
      .waitForFunction(() => {
        const pager = document.querySelector('[data-testid="home-pages"]');
        if (!pager) return false;
        const pages = pager.scrollLeft / pager.clientWidth;
        return Math.abs(pages - Math.round(pages)) * pager.clientWidth < 2;
      }, null, { timeout: TIMEOUT })
      .catch(async (err) => {
        const at = await page.getByTestId('home-pages').evaluate((el) => ({ left: el.scrollLeft, width: el.clientWidth }));
        throw new Error(`the pager never settled on a page: ${JSON.stringify(at)} (${err.message})`);
      });
  const swipe = async (dx) => {
    await page.getByTestId('home-pages').hover();
    await page.mouse.wheel(dx, 0);
  };
  const inView = async (locator) => {
    const [box, pager] = [await locator.boundingBox(), await page.getByTestId('home-pages').boundingBox()];
    return box !== null && pager !== null && box.x >= pager.x - 1 && box.x + box.width <= pager.x + pager.width + 1;
  };
  /** The tiles on a space page, in grid order. */
  const tiles = (slug) =>
    page.getByTestId(`apps-space-${slug}`).evaluate((root) =>
      [...root.querySelectorAll('[data-testid^="home-open-"], [data-testid^="apps-open-"], [data-testid="apps-add"]')]
        .map((el) => el.getAttribute('data-testid'))
        .filter((id) => !id.endsWith('-badge')),
    );

  // --- 1. a cold launch lands on Chat, in an empty chat ---------------------
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
  await loginThroughUi(page, USER);
  await page.locator('[data-testid="chat-view"][data-thread-id=""]').waitFor({ state: 'attached', timeout: TIMEOUT });
  check(new URL(page.url()).pathname === '/', 'sign-in lands on the home pager', page.url());
  await selected('home-page-chat');
  ok('the Chat page is current, in a new, empty chat (not the newest seeded one)');
  check(
    (await page.getByRole('tab').count()) === (await page.getByTestId('home-page-indicator').getByRole('tab').count()),
    'no tab bar, just the page indicator',
  );
  check((await page.getByTestId('new-chat-header-button').count()) === 0, 'no + in the header');
  await page.getByTestId('home-page-chat-badge').getByText('2').waitFor({ timeout: TIMEOUT });
  ok("the Chat dot's badge counts the two chats that need you");
  if ((await page.getByTestId('chat-mic').count()) > 0) {
    check(await page.getByTestId('chat-voice-start').isVisible(), 'an empty chat shows the big mic');
  } else {
    ok("(no speech recognition in this browser, so no mic; the big mic is jest's)");
  }

  // --- 2. the drawer: "needs you" order, five then More, New chat at the foot
  await page.getByTestId('chat-history-button').click();
  const drawer = page.getByTestId('chat-drawer');
  await drawer.waitFor({ state: 'visible', timeout: TIMEOUT });
  await drawer.getByTestId('thread-row').filter({ hasText: CHATS.plain.title }).waitFor({ timeout: TIMEOUT });
  let rows = await drawer.getByTestId('thread-row').allInnerTexts();
  const at = (title) => rows.findIndex((text) => text.includes(title));
  check(
    at(CHATS.needs.title) === 0 && at(CHATS.unread.title) === 1 && at(CHATS.plain.title) === 2,
    'the drawer lists the chat waiting on you, then the unread one, then the newest plain one',
    rows,
  );
  check(rows.length === 5 && at(OLDEST) === -1, 'the drawer shows five chats', rows);
  check((await drawer.getByTestId(`thread-needs-approval-${CHATS.needs.id}`).count()) === 1, 'the waiting chat is marked');
  check((await drawer.getByTestId(`thread-unread-${CHATS.unread.id}`).count()) === 1, 'the unread chat is marked');
  const newChat = drawer.getByTestId('chat-drawer-new-chat');
  const [panel, before] = [await drawer.boundingBox(), await newChat.boundingBox()];
  check(before.y + before.height > panel.y + panel.height - 80 && before.width > panel.width * 0.8, 'New chat spans the foot of the drawer', { panel, before });
  await drawer.getByTestId('chat-drawer-more').click();
  await drawer.getByTestId('thread-row').filter({ hasText: OLDEST }).waitFor({ timeout: TIMEOUT });
  rows = await drawer.getByTestId('thread-row').allInnerTexts();
  check(rows.length === 6, 'More lists the rest', rows);
  await drawer.getByTestId('thread-row').filter({ hasText: OLDEST }).hover();
  await page.mouse.wheel(0, 600);
  await page.waitForTimeout(300);
  const after = await newChat.boundingBox();
  check(Math.abs(after.y - before.y) < 1, "scrolling the list doesn't move New chat", { before, after });
  await drawer.getByTestId('thread-row').filter({ hasText: CHATS.plain.title }).click();
  await drawer.waitFor({ state: 'hidden', timeout: TIMEOUT });
  check((await currentThreadId(page)) === CHATS.plain.id, 'a chat opens from the drawer');

  await page.getByTestId('chat-history-button').click();
  await drawer.waitFor({ state: 'visible', timeout: TIMEOUT });
  check((await drawer.getByTestId('thread-row').count()) === 5, 'reopened, the drawer is back to five');
  await newChat.click();
  await drawer.waitFor({ state: 'hidden', timeout: TIMEOUT });
  await page.locator('[data-testid="chat-view"][data-thread-id=""]').waitFor({ state: 'attached', timeout: TIMEOUT });
  ok("the drawer's New chat makes a new, empty chat");

  // --- 3. swipe: Chat -> Personal -> the shared space ----------------------
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
  await page.reload({ waitUntil: 'domcontentloaded' });
  const sharedDot = page.getByTestId(`home-space-${SLUG}`);
  await sharedDot.waitFor({ timeout: TIMEOUT });
  await selected('home-page-chat');
  const dots = page.getByTestId('home-page-indicator').getByRole('tab');
  check((await dots.count()) === 3, 'the indicator is Chat, Personal, the shared space');
  const personalId = await dots.nth(1).getAttribute('data-testid');
  const personalSlug = personalId.replace(/^home-space-/, '');
  check(personalSlug !== SLUG, 'Personal is the page after Chat');

  await swipe(300);
  await selected(personalId);
  await settled();
  check(await inView(page.getByTestId(`apps-space-${personalSlug}`)), 'a swipe from Chat reaches Personal');
  check(await page.getByRole('heading', { name: 'Personal' }).first().isVisible(), 'the title is Personal');
  let order = await tiles(personalSlug);
  check(
    JSON.stringify(order) === JSON.stringify(['home-open-files', 'home-open-routines', 'home-open-settings', 'apps-add']),
    'Personal is Files, Routines, Settings, then Add app',
    order,
  );

  await swipe(300);
  await selected(`home-space-${SLUG}`);
  await settled();
  const tile = page.getByTestId(`apps-space-${SLUG}`).getByTestId('apps-open-runtime-check');
  check(await inView(tile), 'another swipe reaches the shared space, with its app on screen');
  const hasIcon = (await tile.getByTestId('app-icon-glyph-runtime-check').count()) + (await tile.getByTestId('app-icon-letter-runtime-check').count());
  check(hasIcon === 1, 'the app is an icon tile');
  check(await page.getByRole('heading', { name: SPACE_NAME }).first().isVisible(), "the title is the space's name");
  order = await tiles(SLUG);
  check(
    JSON.stringify(order) === JSON.stringify(['home-open-files', 'apps-open-runtime-check', 'apps-add']),
    'the shared space is Files, its app, then Add app (no Routines or Settings)',
    order,
  );

  // --- 4. Add app opens just that space's catalog ---------------------------
  const sharedPage = page.getByTestId(`apps-space-${SLUG}`);
  await sharedPage.getByTestId('apps-add').click();
  await page.waitForURL((url) => url.pathname === '/apps/catalog', { timeout: TIMEOUT });
  check(new URL(page.url()).searchParams.get('spaceId') === shared.id, "Add app opens that space's catalog", page.url());
  await page.getByTestId('catalog-empty').getByText(`Nothing to add to ${SPACE_NAME} yet`).waitFor({ timeout: TIMEOUT });
  ok('with nothing published there, it says so for that space');
  check(await page.getByRole('heading', { name: `Add to ${SPACE_NAME}` }).first().isVisible(), 'the catalog is titled for the space');
  await page.goBack();
  await page.waitForURL((url) => url.pathname === '/', { timeout: TIMEOUT });
  await selected(`home-space-${SLUG}`);
  ok('back from the catalog returns to the shared page');

  // --- 5. Files, Routines and Settings open from their tiles --------------
  const backHome = async (button, dotId) => {
    await button.click();
    await page.waitForURL((url) => url.pathname === '/', { timeout: TIMEOUT });
    await selected(dotId);
  };
  await sharedPage.getByTestId('home-open-files').click();
  await page.waitForURL((url) => url.pathname === '/files', { timeout: TIMEOUT });
  check(new URL(page.url()).searchParams.get('path') === `/spaces/${SLUG}`, "Files opens at the page's space", page.url());
  await page.getByTestId('files-screen').getByText('Apps', { exact: true }).first().waitFor({ timeout: TIMEOUT });
  await backHome(page.getByTestId('back-to-apps'), `home-space-${SLUG}`);
  ok('back from Files returns to the same page');

  await dots.nth(1).click();
  await selected(personalId);
  await settled();
  const personalPage = page.getByTestId(`apps-space-${personalSlug}`);
  await personalPage.getByTestId('home-open-routines').click();
  await page.getByTestId('routines-screen').waitFor({ timeout: TIMEOUT });
  ok('the Routines tile opens Routines');
  await backHome(page.getByTestId('routines-screen').getByTestId('settings-back-button'), personalId);
  await personalPage.getByTestId('home-open-settings').click();
  await page.getByTestId('settings-account').waitFor({ timeout: TIMEOUT });
  check((await page.getByTestId('settings-account').innerText()).includes(USER.username), 'the Settings tile opens Settings');
  await backHome(page.getByTestId('settings-screen').getByTestId('settings-back-button'), personalId);
  ok('back from Routines and Settings returns to Personal');

  // --- 6. back to Chat: swipe right, or the Chat dot -----------------------
  await swipe(-300);
  await selected('home-page-chat');
  await settled();
  check(await inView(page.getByTestId('home-page-chat-content')), 'swiping back from Personal reaches Chat');
  await sharedDot.click();
  await selected(`home-space-${SLUG}`);
  await page.getByTestId('home-page-chat').click();
  await selected('home-page-chat');
  await settled();
  await page.locator('[data-testid="chat-view"][data-thread-id=""]').waitFor({ state: 'attached', timeout: TIMEOUT });
  ok('the Chat dot returns to the same chat');

  console.log('PASS: G19 app shell smoke');
} finally {
  await browser.close();
}
