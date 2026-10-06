// M14-02, M19-01, M19-05: the app shell — sign-in lands in Chat; the tab bar
// is Chat and Apps; Apps has the system dock (Files, Routines, Settings,
// Catalog), each opening its app with back returning to Apps; each space is a
// page, swiped between, with the right apps on each and Files opening at the
// page's space. Driven by home_launcher_smoke.sh.
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

import { FIXTURE, fixtureFiles } from './app_fixture.mjs';
import { createE2eUser, deleteE2eAppFiles, deleteE2eSpaces, deleteE2eUsers, loginThroughUi } from './auth_helpers.mjs';
import { openSystemApp } from './nav_helpers.mjs';

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
  await page.getByTestId('new-chat-header-button').waitFor({ timeout: TIMEOUT_MS });
  check(new URL(page.url()).pathname.startsWith('/chat'), 'signed in lands in Chat', page.url());
  const tabs = await page.getByRole('tab').allInnerTexts();
  check(
    tabs.length === 2 && tabs[0].includes('Chat') && tabs[1].includes('Apps'),
    'the tab bar is just Chat and Apps',
    JSON.stringify(tabs),
  );

  await page.getByRole('tab', { name: 'Apps' }).click();
  await page.getByTestId('home-launcher').waitFor({ timeout: TIMEOUT_MS });
  for (const slug of ['files', 'routines', 'settings']) {
    check(await page.getByTestId(`home-open-${slug}`).isVisible(), `${slug} is a system tile`);
  }
  check((await page.getByTestId('home-open-chat').count()) === 0, 'Chat is a tab, not a tile');
  check(await page.getByTestId('apps-catalog').isVisible(), 'catalog is on Apps');
  check(await page.getByTestId('apps-empty').isVisible(), 'no installed apps yet');

  check((await page.getByTestId('home-space-dots').count()) === 0, 'no page dots with only Personal');

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

  await page.goto(`${BASE}/apps`, { waitUntil: 'domcontentloaded' });
  const personalPage = page.locator('[data-testid^="apps-space-"]').first();
  const sharedPage = page.getByTestId(`apps-space-${SLUG}`);
  const sharedDot = page.getByTestId(`home-space-${SLUG}`);
  await sharedDot.waitFor({ timeout: TIMEOUT_MS });
  check((await personalPage.getAttribute('data-testid')) !== `apps-space-${SLUG}`, 'Personal is the first page');
  check(await personalPage.getByTestId('apps-empty').isVisible(), 'the Personal page has no apps');
  check((await personalPage.getByTestId('apps-open-runtime-check').count()) === 0, "the shared space's app isn't on Personal");
  check((await sharedDot.getAttribute('aria-selected')) === 'false', 'the shared page is not current yet');
  const inView = async (locator) => {
    const [box, pager] = [await locator.boundingBox(), await page.getByTestId('home-pages').boundingBox()];
    return box !== null && pager !== null && box.x >= pager.x - 1 && box.x + box.width <= pager.x + pager.width + 1;
  };
  check(!(await inView(sharedPage)), 'the shared page starts off screen');

  await page.getByTestId('home-pages').hover();
  await page.mouse.wheel(1200, 0);
  await page.waitForFunction((slug) => document.querySelector(`[data-testid="home-space-${slug}"]`)?.getAttribute('aria-selected') === 'true', SLUG, { timeout: TIMEOUT_MS });
  ok('a horizontal swipe moved to the shared page');
  await page.waitForFunction(() => {
    const pager = document.querySelector('[data-testid="home-pages"]');
    return pager && pager.scrollLeft > 0 && pager.scrollLeft % pager.clientWidth < 2;
  }, null, { timeout: TIMEOUT_MS });
  check(await inView(sharedPage), 'the shared page is on screen');
  check(await sharedPage.getByTestId('apps-open-runtime-check').isVisible(), "the shared page shows the space's app");
  check(await page.getByRole('heading', { name: `E2E Home ${process.pid}` }).first().isVisible(), "the title is the space's name");

  await page.getByTestId('home-open-files').click();
  await page.waitForURL((url) => url.pathname === '/files', { timeout: TIMEOUT_MS });
  check(new URL(page.url()).searchParams.get('path') === `/spaces/${SLUG}`, "Files opens at the page's space", page.url());
  await page.getByTestId('files-screen').getByText('Apps', { exact: true }).first().waitFor({ timeout: TIMEOUT_MS });
  await page.getByTestId('back-to-apps').click();
  await page.waitForURL((url) => url.pathname === '/apps', { timeout: TIMEOUT_MS });
  check((await sharedDot.getAttribute('aria-selected')) === 'true', 'back on Apps, the shared page is still current');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await sharedDot.waitFor({ timeout: TIMEOUT_MS });
  await page.waitForFunction((slug) => document.querySelector(`[data-testid="home-space-${slug}"]`)?.getAttribute('aria-selected') === 'true', SLUG, { timeout: TIMEOUT_MS });
  check(await inView(sharedPage), 'a reload keeps the shared page (session)');

  await page.locator('[data-testid^="home-space-"][role="tab"]').first().click();
  await page.waitForFunction(() => document.querySelector('[data-testid="home-space-dots"] [role="tab"]')?.getAttribute('aria-selected') === 'true', null, { timeout: TIMEOUT_MS });
  ok('tapping the first dot returns to Personal');

  await page.getByTestId('apps-catalog').click();
  await page.getByTestId('catalog-empty').waitFor({ timeout: TIMEOUT_MS });
  ok('catalog opened from Apps');
  await page.getByRole('tab', { name: 'Apps' }).click();
  await page.getByTestId('home-launcher').waitFor({ timeout: TIMEOUT_MS });

  const backToApps = async (button) => {
    await button.click();
    await page.waitForURL((url) => url.pathname === '/apps', { timeout: TIMEOUT_MS });
    await page.getByTestId('home-launcher').waitFor({ timeout: TIMEOUT_MS });
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

  await page.getByRole('tab', { name: 'Chat' }).click();
  await page.getByTestId('new-chat-header-button').waitFor({ timeout: TIMEOUT_MS });
  ok('the Chat tab opens Chat');
  console.log(`PASS: home launcher smoke in ${Date.now() - startedAt}ms`);
} finally {
  if (browser) await browser.close();
  deleteE2eSpaces(SLUG);
  deleteE2eAppFiles(appId);
  deleteE2eUsers(member.username);
}
