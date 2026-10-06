// M14-01: publish → family member installs → author updates → member approves.
// Driven by app_publish_smoke.sh; see its header.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

import { fixtureFiles } from './app_fixture.mjs';
import { loginThroughUi } from './auth_helpers.mjs';
import { openAppSheet } from './nav_helpers.mjs';

const APP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), 'fixtures/apps/hello');
const APP_SLUG = 'hello';
const BASE = (process.env.PUBLISH_SMOKE_BASE_URL ?? 'http://localhost/').replace(/\/$/, '');
const AUTHOR = { username: process.env.PUBLISH_SMOKE_USER, password: process.env.PUBLISH_SMOKE_PASSWORD };
const EDITOR = { username: process.env.PUBLISH_SMOKE_EDITOR, password: process.env.PUBLISH_SMOKE_EDITOR_PASSWORD };
const SLUG = process.env.PUBLISH_SMOKE_SPACE_SLUG;
const STATE = process.env.PUBLISH_SMOKE_STATE;
if (
  ![AUTHOR, EDITOR].every((u) => /^e2e-/.test(u.username ?? '') && u.password) ||
  !/^e2e-pub-/.test(SLUG ?? '') ||
  !STATE
) {
  throw new Error('PUBLISH_SMOKE_USER / _EDITOR (e2e-*), their passwords, PUBLISH_SMOKE_SPACE_SLUG (e2e-pub-*) and PUBLISH_SMOKE_STATE are required');
}
const UI_TIMEOUT = 30_000;

const ok = (what) => console.log(`ok   ${what}`);
function check(cond, what, detail) {
  if (!cond) throw new Error(`FAIL: ${what}${detail === undefined ? '' : `: ${JSON.stringify(detail)}`}`);
  ok(what);
}

function api(context) {
  return async (method, url, { json, raw, timeout = 30_000, allowStatus } = {}) => {
    const r = await context.request.fetch(`${BASE}${url}`, {
      method,
      timeout,
      ...(raw !== undefined ? { data: raw, headers: { 'Content-Type': 'application/octet-stream' } } : json !== undefined ? { data: json } : {}),
    });
    const text = await r.text();
    if (allowStatus !== undefined && r.status() === allowStatus) return { status: r.status(), body: text ? JSON.parse(text) : null };
    if (!r.ok()) throw new Error(`${method} ${url}: ${r.status()} ${text}`);
    return text ? JSON.parse(text) : null;
  };
}

async function signIn(browser, user) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${BASE}/`);
  await loginThroughUi(page, user);
  return { context, page, call: api(context) };
}

async function uploadAndBuild(call, space) {
  const root = space.kind === 'personal' ? '/personal' : `/spaces/${space.slug}`;
  const dir = `${root}/Apps/${APP_SLUG}`;
  for (const rel of fixtureFiles(APP)) {
    await call('PUT', `/api/platform/files/content?path=${encodeURIComponent(`${dir}/${rel}`)}`, {
      raw: fs.readFileSync(path.join(APP, rel)),
    });
  }
  const { app } = await call('POST', '/api/platform/apps', { json: { source_path: dir } });
  fs.appendFileSync(STATE, `${app.id}\n`);
  await call('POST', `/api/platform/spaces/${space.id}/instances`, { json: { app_id: app.id } });
  const built = await call('POST', `/api/platform/apps/${app.id}/build`, { json: {}, timeout: 180_000 });
  check(built.ok && built.diagnostics.length === 0, `built ${APP_SLUG} ${app.working_version.version}`, built.diagnostics);
  return app;
}

async function publishFromInfo(page, spaceSlug) {
  await page.getByRole('tab', { name: 'Apps' }).click();
  await page.getByTestId(`apps-open-${APP_SLUG}`).first().click();
  await page.getByTestId('app-info-button').click();
  await page.getByTestId('app-publish').waitFor({ timeout: UI_TIMEOUT });
  const toggle = page.getByTestId(`app-publish-space-${spaceSlug}`);
  await toggle.waitFor({ timeout: UI_TIMEOUT });
  if ((await toggle.innerText()).includes('Off')) await toggle.click();
  await page.getByTestId('app-publish-confirm').click();
  await page.getByTestId('app-info-notice').waitFor({ timeout: UI_TIMEOUT });
  ok(`published from app info into ${spaceSlug}`);
}

const browser = await chromium.launch();
try {
  const author = await signIn(browser, AUTHOR);
  ok(`signed in as ${AUTHOR.username}`);
  const personal = (await author.call('GET', '/api/platform/spaces')).spaces.find((s) => s.kind === 'personal');
  const shared = await author.call('POST', '/api/platform/spaces', { json: { slug: SLUG, name: `E2E Publish ${process.pid}` } });
  const directory = (await author.call('GET', '/api/platform/users/directory')).users;
  const editorUser = directory.find((u) => u.username === EDITOR.username);
  await author.call('POST', `/api/platform/spaces/${shared.id}/members`, { json: { user_id: editorUser.id, role: 'editor' } });
  const app = await uploadAndBuild(author.call, personal);
  // Home was already open (empty) during the REST install; reload so the
  // new instance is on the launcher (same pattern as home_launcher_smoke).
  await author.page.goto(`${BASE}/apps`);

  await publishFromInfo(author.page, shared.slug);

  const editor = await signIn(browser, EDITOR);
  ok(`signed in as ${EDITOR.username}`);
  await editor.page.getByRole('tab', { name: 'Apps' }).click();
  await editor.page.getByTestId('apps-catalog').click();
  await editor.page.getByTestId(`catalog-app-${APP_SLUG}`).waitFor({ timeout: UI_TIMEOUT });
  await editor.page.getByTestId(`catalog-app-${APP_SLUG}`).click();
  await editor.page.getByTestId('apps-install').waitFor({ timeout: UI_TIMEOUT });
  check((await editor.page.getByTestId('install-permissions').innerText()).includes("doesn't request extra permissions"), 'install sheet lists empty permissions');
  await editor.page.getByTestId('install-confirm').click();
  await editor.page.getByTestId('app-runner').waitFor({ timeout: UI_TIMEOUT });
  ok('editor installed from the catalog');

  const manifest = JSON.parse(fs.readFileSync(path.join(APP, 'app.json'), 'utf8'));
  manifest.version = '1.1.0';
  await author.call('PUT', `/api/platform/files/content?path=${encodeURIComponent(`/personal/Apps/${APP_SLUG}/app.json`)}`, {
    raw: Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`),
  });
  const validated = await author.call('POST', `/api/platform/apps/${app.id}/validate`);
  check(validated.valid === true, 'validated 1.1.0');
  const rebuilt = await author.call('POST', `/api/platform/apps/${app.id}/build`, { json: {}, timeout: 180_000 });
  check(rebuilt.ok && rebuilt.diagnostics.length === 0, 'rebuilt 1.1.0', rebuilt.diagnostics);
  await author.page.goto(`${BASE}/apps/info/${app.id}`);
  await author.page.getByTestId('app-publish').waitFor({ timeout: UI_TIMEOUT });
  const toggle = author.page.getByTestId(`app-publish-space-${shared.slug}`);
  if ((await toggle.innerText()).includes('Off')) await toggle.click();
  await author.page.getByTestId('app-publish-confirm').click();
  await author.page.getByTestId('app-info-notice').waitFor({ timeout: UI_TIMEOUT });
  ok('published 1.1.0');

  await editor.page.goto(`${BASE}/apps`);
  await editor.page.getByTestId(`apps-open-${APP_SLUG}-badge`).waitFor({ timeout: UI_TIMEOUT });
  await openAppSheet(editor.page, editor.page.getByTestId(`apps-open-${APP_SLUG}`).first());
  await editor.page.getByTestId(`apps-update-${APP_SLUG}`).click();
  await editor.page.getByTestId('apps-update').waitFor({ timeout: UI_TIMEOUT });
  await editor.page.getByTestId('update-confirm').click();
  await editor.page.getByTestId('app-runner').waitFor({ timeout: UI_TIMEOUT });
  const listing = await editor.call('GET', `/api/platform/spaces/${shared.id}/instances`);
  const inst = listing.instances.find((i) => i.app.slug === APP_SLUG);
  check(inst && inst.app.version === '1.1.0' && inst.update === null, 'editor is on 1.1.0', inst);
} finally {
  await browser.close();
}
