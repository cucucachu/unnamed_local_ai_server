// M14-02, M19-01: the app shell — sign-in lands in Chat; the tab bar is Chat
// and Apps; Apps has the system tiles (Files, Routines, Settings), the space
// switcher and the catalog; each tile opens its app and back returns to Apps.
// Driven by home_launcher_smoke.sh.
import { chromium } from 'playwright';

import { createE2eUser, deleteE2eSpaces, deleteE2eUsers, loginThroughUi } from './auth_helpers.mjs';
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

  const created = await context.request.post(`${BASE}/api/platform/spaces`, {
    data: { slug: SLUG, name: `E2E Home ${process.pid}` },
  });
  check(created.ok(), `created shared space ${SLUG}`, await created.text());
  await page.goto(`${BASE}/apps`, { waitUntil: 'domcontentloaded' });
  await page.getByTestId('home-space-switcher').waitFor({ timeout: TIMEOUT_MS });
  await page.getByTestId(`home-space-${SLUG}`).click();
  await page.getByTestId('apps-empty').waitFor({ timeout: TIMEOUT_MS });
  check(await page.getByTestId('home-system').isVisible(), 'system tiles stay visible after switching space');
  await page.getByTestId('home-space-all').click();

  await page.getByTestId('apps-catalog').click();
  await page.getByTestId('catalog-empty').waitFor({ timeout: TIMEOUT_MS });
  ok('catalog opened from Apps');
  await page.getByRole('tab', { name: 'Apps' }).click();
  await page.getByTestId('home-launcher').waitFor({ timeout: TIMEOUT_MS });

  const backToApps = async (button) => {
    await page.getByTestId(button).click();
    await page.getByTestId('home-launcher').waitFor({ timeout: TIMEOUT_MS });
  };

  await openSystemApp(page, 'files');
  await page.getByTestId('files-refresh-button').waitFor({ timeout: TIMEOUT_MS });
  check(new URL(page.url()).pathname === '/files', 'the Files tile opens /files', page.url());
  await backToApps('back-to-apps');
  ok('back from Files returns to Apps');

  await openSystemApp(page, 'routines');
  await page.getByTestId('routines-screen').waitFor({ timeout: TIMEOUT_MS });
  await backToApps('settings-back-button');
  ok('the Routines tile opens Routines; back returns to Apps');

  await openSystemApp(page, 'settings');
  await page.getByTestId('settings-account').waitFor({ timeout: TIMEOUT_MS });
  check((await page.getByTestId('settings-account').innerText()).includes(member.username), 'Settings shows the account');
  await backToApps('settings-back-button');
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
  deleteE2eUsers(member.username);
}
