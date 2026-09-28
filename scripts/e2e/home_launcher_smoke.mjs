// M14-02: Home launcher navigation — default tab, system tiles, space
// switcher, catalog, Chat/Files/Settings tabs. Driven by home_launcher_smoke.sh.
import { chromium } from 'playwright';

import { createE2eUser, deleteE2eSpaces, deleteE2eUsers, loginThroughUi } from './auth_helpers.mjs';

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
  await page.getByTestId('home-launcher').waitFor({ timeout: TIMEOUT_MS });
  check(/\/(apps)?$/.test(new URL(page.url()).pathname), 'signed in lands on Home', page.url());
  for (const name of ['Home', 'Chat', 'Files', 'Settings']) {
    check(await page.getByRole('tab', { name }).isVisible(), `${name} tab is in the tab bar`);
  }
  check(await page.getByTestId('home-open-chat').isVisible(), 'Chat is a system tile');
  check(await page.getByTestId('home-open-files').isVisible(), 'Files is a system tile');
  check(await page.getByTestId('home-open-settings').isVisible(), 'Settings is a system tile');
  check(await page.getByTestId('apps-catalog').isVisible(), 'catalog is on Home');
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
  ok('catalog opened from Home');
  await page.getByRole('tab', { name: 'Home' }).click();
  await page.getByTestId('home-launcher').waitFor({ timeout: TIMEOUT_MS });

  await page.getByRole('tab', { name: 'Chat' }).click();
  await page.getByTestId('new-chat-header-button').waitFor({ timeout: TIMEOUT_MS });
  ok('Chat tab opens the thread list');

  await page.getByRole('tab', { name: 'Files' }).click();
  await page.getByTestId('files-refresh-button').waitFor({ timeout: TIMEOUT_MS });
  check(new URL(page.url()).pathname === '/files', 'Files tab is /files', page.url());

  await page.getByRole('tab', { name: 'Settings' }).click();
  await page.getByTestId('settings-account').waitFor({ timeout: TIMEOUT_MS });
  check((await page.getByTestId('settings-account').innerText()).includes(member.username), 'Settings tab shows the account');

  await page.getByRole('tab', { name: 'Home' }).click();
  await page.getByTestId('home-open-chat').click();
  await page.getByTestId('new-chat-header-button').waitFor({ timeout: TIMEOUT_MS });
  ok('Home Chat tile opens Chat');
  console.log(`PASS: home launcher smoke in ${Date.now() - startedAt}ms`);
} finally {
  if (browser) await browser.close();
  deleteE2eSpaces(SLUG);
  deleteE2eUsers(member.username);
}
