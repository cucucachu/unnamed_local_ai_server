// M22-05: start a new app from a template in the catalog.
// Driven by app_template_smoke.sh; see its header.
import fs from 'node:fs';
import { chromium } from 'playwright';

import { loginThroughUi } from './auth_helpers.mjs';
import { openSpacePage } from './nav_helpers.mjs';

const BASE = (process.env.TEMPLATE_SMOKE_BASE_URL ?? 'http://localhost/').replace(/\/$/, '');
const USER = { username: process.env.TEMPLATE_SMOKE_USER, password: process.env.TEMPLATE_SMOKE_PASSWORD };
const STATE = process.env.TEMPLATE_SMOKE_STATE;
if (!/^e2e-/.test(USER.username ?? '') || !USER.password || !STATE) {
  throw new Error('TEMPLATE_SMOKE_USER (e2e-*), TEMPLATE_SMOKE_PASSWORD and TEMPLATE_SMOKE_STATE are required');
}
const APP_FRAME = 'iframe[title="app"]';
const UI_TIMEOUT = 30_000;
const BUILD_TIMEOUT = 180_000;

const ok = (what) => console.log(`ok   ${what}`);
function check(cond, what, detail) {
  if (!cond) throw new Error(`FAIL: ${what}${detail === undefined ? '' : `: ${JSON.stringify(detail)}`}`);
  ok(what);
}

const browser = await chromium.launch();
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  const call = async (url) => {
    const r = await context.request.get(`${BASE}${url}`);
    if (!r.ok()) throw new Error(`GET ${url}: ${r.status()} ${await r.text()}`);
    return r.json();
  };
  await page.goto(`${BASE}/`);
  await loginThroughUi(page, USER);
  ok(`signed in as ${USER.username}`);

  const { templates } = await call('/api/platform/app-templates');
  check(templates.some((t) => t.slug === 'tracker'), 'the tracker template is listed', templates.map((t) => t.slug));

  const personal = await openSpacePage(page);
  await personal.getByTestId('apps-add').click();
  await page.getByTestId('catalog-template-tracker').click();
  await page.getByTestId('apps-template').waitFor({ timeout: UI_TIMEOUT });
  await page.getByTestId('template-name').fill('E2E Habits');
  check((await page.getByTestId('template-folder').innerText()).includes('Apps/e2e-habits'), 'the folder follows the name');
  await page.getByTestId('template-create').click();
  await page.getByTestId('app-runner').waitFor({ timeout: BUILD_TIMEOUT });

  const space = (await call('/api/platform/spaces')).spaces.find((s) => s.kind === 'personal');
  const inst = (await call(`/api/platform/spaces/${space.id}/instances`)).instances.find((i) => i.app.slug === 'e2e-habits');
  check(inst && inst.tracks === 'working', 'installed the working copy', inst);
  fs.appendFileSync(STATE, `${inst.app_id}\n`);
  ok('created E2E Habits from the catalog and opened it');

  const ui = page.frameLocator(APP_FRAME);
  await ui.getByTestId('summary').waitFor({ timeout: BUILD_TIMEOUT });
  await ui.getByTestId('new-habit-name').fill('Stretch');
  await ui.getByTestId('add-habit').click();
  await ui.getByText('Stretch').waitFor({ timeout: UI_TIMEOUT });
  check((await ui.getByTestId('summary').innerText()).includes('of 1 today'), 'the built app runs its actions');

  const manifest = await call(`/api/platform/files/download?path=${encodeURIComponent('/personal/Apps/e2e-habits/app.json')}`);
  check(
    manifest.slug === 'e2e-habits' && manifest.name === 'E2E Habits' && manifest.homeai?.template?.slug === 'tracker',
    'the copy records its template',
    { slug: manifest.slug, name: manifest.name, template: manifest.homeai?.template },
  );
} finally {
  await browser.close();
}
