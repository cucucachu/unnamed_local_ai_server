// M14-02, M19-01, M19-05, M19-07: the app shell — sign-in lands on the Chat
// page of the home pager; Personal is the next page, with Files, Routines,
// Settings and Add app tiles, each opening its app with back returning to the
// same page; each shared space is a further page, swiped to, with the right
// apps on each and Files opening at the page's space; the old /apps and /chat
// links land on their pages. Driven by home_launcher_smoke.sh.
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

import { FIXTURE, fixtureFiles } from './app_fixture.mjs';
import { createE2eUser, deleteE2eAppFiles, deleteE2eSpaces, deleteE2eUsers, loginThroughUi } from './auth_helpers.mjs';
import { openSpacePage, openSystemApp } from './nav_helpers.mjs';

const BASE = (process.env.HOME_SMOKE_BASE_URL ?? process.env.E2E_BASE ?? 'http://localhost/').replace(/\/$/, '');
const TIMEOUT_MS = 30_000;
const SLUG = `e2e-home-${Date.now().toString(36)}`;

const ok = (what) => console.log(`ok   ${what}`);
function check(cond, what, detail) {
  if (!cond) throw new Error(`FAIL: ${what}${detail === undefined ? '' : `: ${JSON.stringify(detail)}`}`);
  ok(what);
}

const startedAt = Date.now();
const member = createE2eUser({ prefix: 'e2e-home' });
let browser;
let appId = null;
try {
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
  await loginThroughUi(page, member);
  await page.getByTestId('auth-authenticated').waitFor({ timeout: TIMEOUT_MS });
  await page.getByTestId('home-page-chat').waitFor({ timeout: TIMEOUT_MS });
  check(new URL(page.url()).pathname === '/', 'signed in lands on the home pager', page.url());
  const dots = page.getByTestId('home-page-indicator').getByRole('tab');
  check((await dots.count()) === 2, 'with only Personal, the indicator is Chat and Personal');
  check((await dots.nth(0).getAttribute('aria-selected')) === 'true', 'the Chat page is current');

  const personal = await openSpacePage(page);
  for (const slug of ['files', 'routines', 'settings']) {
    check(await personal.getByTestId(`home-open-${slug}`).isVisible(), `${slug} is a tile on Personal`);
  }
  check((await page.getByTestId('home-open-chat').count()) === 0, 'Chat is a page, not a tile');
  check(await personal.getByTestId('apps-add').isVisible(), 'Add app is on Personal');
  check(await personal.getByTestId('apps-empty').isVisible(), 'no installed apps yet');

  // --- M19-05: a shared space with an app is a second page -----------------
  const call = async (method, url, options = {}) => {
    const r = await context.request.fetch(`${BASE}${url}`, { method, ...options });
    const text = await r.text();
    if (!r.ok()) throw new Error(`${method} ${url}: ${r.status()} ${text}`);
    return text ? JSON.parse(text) : null;
  };
  const shared = await call('POST', '/api/platform/spaces', { data: { slug: SLUG, name: `E2E Home ${process.pid}` } });
  const appDir = `/spaces/${SLUG}/Apps/runtime-check`;
  for (const rel of fixtureFiles()) {
    await call('PUT', `/api/platform/files/content?path=${encodeURIComponent(`${appDir}/${rel}`)}`, {
      data: fs.readFileSync(path.join(FIXTURE, rel)),
      headers: { 'Content-Type': 'application/octet-stream' },
    });
  }
  appId = (await call('POST', '/api/platform/apps', { data: { source_path: appDir } })).app.id;
  await call('POST', `/api/platform/spaces/${shared.id}/instances`, { data: { app_id: appId } });
  ok(`shared space ${SLUG} with runtime-check installed`);

  await page.reload({ waitUntil: 'domcontentloaded' });
  const personalPage = page.locator('[data-testid^="apps-space-"]').first();
  const sharedPage = page.getByTestId(`apps-space-${SLUG}`);
  const sharedDot = page.getByTestId(`home-space-${SLUG}`);
  await sharedDot.waitFor({ timeout: TIMEOUT_MS });
  check((await personalPage.getAttribute('data-testid')) !== `apps-space-${SLUG}`, 'Personal is the first space page');
  check(await personalPage.getByTestId('apps-empty').isVisible(), 'the Personal page has no apps');
  check((await personalPage.getByTestId('apps-open-runtime-check').count()) === 0, "the shared space's app isn't on Personal");
  check((await sharedDot.getAttribute('aria-selected')) === 'false', 'the shared page is not current yet');
  const inView = async (locator) => {
    const [box, pager] = [await locator.boundingBox(), await page.getByTestId('home-pages').boundingBox()];
    return box !== null && pager !== null && box.x >= pager.x - 1 && box.x + box.width <= pager.x + pager.width + 1;
  };
  check(await inView(personalPage), 'a reload keeps Personal (session)');
  check(!(await inView(sharedPage)), 'the shared page starts off screen');

  const pagerWidth = await page.getByTestId('home-pages').evaluate((el) => el.clientWidth);
  await page.getByTestId('home-pages').hover();
  await page.mouse.wheel(pagerWidth * 0.8, 0);
  await page.waitForFunction((slug) => document.querySelector(`[data-testid="home-space-${slug}"]`)?.getAttribute('aria-selected') === 'true', SLUG, { timeout: TIMEOUT_MS });
  ok('a horizontal swipe moved to the shared page');
  await page.waitForFunction(() => {
    const pager = document.querySelector('[data-testid="home-pages"]');
    return pager && pager.scrollLeft > 0 && pager.scrollLeft % pager.clientWidth < 2;
  }, null, { timeout: TIMEOUT_MS });
  check(await inView(sharedPage), 'the shared page is on screen');
  check(await sharedPage.getByTestId('apps-open-runtime-check').isVisible(), "the shared page shows the space's app");
  check((await sharedPage.getByTestId('home-open-routines').count()) === 0, 'Routines and Settings are Personal only');
  check(await page.getByRole('heading', { name: `E2E Home ${process.pid}` }).first().isVisible(), "the title is the space's name");

  await sharedPage.getByTestId('home-open-files').click();
  await page.waitForURL((url) => url.pathname === '/files', { timeout: TIMEOUT_MS });
  check(new URL(page.url()).searchParams.get('path') === `/spaces/${SLUG}`, "Files opens at the page's space", page.url());
  await page.getByTestId('files-screen').getByText('Apps', { exact: true }).first().waitFor({ timeout: TIMEOUT_MS });
  await page.getByTestId('back-to-apps').click();
  await page.waitForURL((url) => url.pathname === '/', { timeout: TIMEOUT_MS });
  check((await sharedDot.getAttribute('aria-selected')) === 'true', 'back home, the shared page is still current');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await sharedDot.waitFor({ timeout: TIMEOUT_MS });
  await page.waitForFunction((slug) => document.querySelector(`[data-testid="home-space-${slug}"]`)?.getAttribute('aria-selected') === 'true', SLUG, { timeout: TIMEOUT_MS });
  check(await inView(sharedPage), 'a reload keeps the shared page (session)');

  await openSpacePage(page);
  ok('tapping the Personal dot returns to Personal');

  await personalPage.getByTestId('apps-add').click();
  await page.getByTestId('catalog-empty').waitFor({ timeout: TIMEOUT_MS });
  ok("Add app opens Personal's catalog");

  const backToApps = async (button) => {
    await button.click();
    await page.waitForURL((url) => url.pathname === '/', { timeout: TIMEOUT_MS });
    await page.getByTestId('home-launcher').waitFor({ timeout: TIMEOUT_MS });
    check(await inView(personalPage), 'back on Personal');
  };

  await openSystemApp(page, 'files');
  await page.getByTestId('files-refresh-button').waitFor({ timeout: TIMEOUT_MS });
  check(new URL(page.url()).pathname === '/files', 'the Files tile opens /files', page.url());
  await backToApps(page.getByTestId('back-to-apps'));
  ok('back from Files returns to Apps');

  await openSystemApp(page, 'routines');
  await page.getByTestId('routines-screen').waitFor({ timeout: TIMEOUT_MS });
  await backToApps(page.getByTestId('routines-screen').getByTestId('settings-back-button'));
  ok('the Routines tile opens Routines; back returns to Apps');

  await openSystemApp(page, 'settings');
  await page.getByTestId('settings-account').waitFor({ timeout: TIMEOUT_MS });
  check((await page.getByTestId('settings-account').innerText()).includes(member.username), 'Settings shows the account');
  await backToApps(page.getByTestId('settings-screen').getByTestId('settings-back-button'));
  ok('back from Settings returns to Apps');

  await page.goto(`${BASE}/settings/routines`, { waitUntil: 'domcontentloaded' });
  await page.getByTestId('routines-screen').waitFor({ timeout: TIMEOUT_MS });
  ok('the old /settings/routines link opens Routines');

  for (const [link, current] of [['/apps', personalPage], ['/chat', page.getByTestId('home-page-chat-content')]]) {
    await page.goto(`${BASE}${link}`, { waitUntil: 'domcontentloaded' });
    await page.waitForURL((url) => url.pathname === '/', { timeout: TIMEOUT_MS });
    await page.waitForFunction((testId) => {
      const pager = document.querySelector('[data-testid="home-pages"]');
      const target = document.querySelector(`[data-testid="${testId}"]`);
      return pager && target && Math.abs(pager.getBoundingClientRect().left - target.getBoundingClientRect().left) < 2;
    }, await current.getAttribute('data-testid'), { timeout: TIMEOUT_MS });
    ok(`the old ${link} link lands on its page`);
  }
  console.log(`PASS: home launcher smoke in ${Date.now() - startedAt}ms`);
} finally {
  if (browser) await browser.close();
  deleteE2eSpaces(SLUG);
  deleteE2eAppFiles(appId);
  deleteE2eUsers(member.username);
}
