// M10-06 web auth smoke — invoked by `auth_browser_smoke.sh`. Real headless
// Chromium against the live stack (Caddy -> platform `/api/auth/*`):
//
//   1. While bootstrap is open, the Setup screen renders with every field
//      (and the setup code is readable via `docker compose exec`). It is
//      NEVER submitted — the maintainer must become the first admin.
//      Once bootstrap is done, the Login screen shows instead.
//   2. A wrong password shows the `invalid_credentials` message.
//   3. A CLI-created member signs in -> the app loads; a reload keeps
//      the session (the `homeai_session` cookie).
//   4. Settings -> Log out -> Login screen; the session is revoked
//      server-side and a reload stays signed out.
//   5. An e2e admin creates an invite; `/invite?token=…` creates a member
//      and lands on Home; reusing the token shows `invalid_invite`.
// Every throwaway account and the invite are deleted on exit.

import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

import { createE2eUser, createInvite, deleteE2eUsers, deleteInvite, loginThroughUi } from './auth_helpers.mjs';
import { openSystemApp } from './nav_helpers.mjs';

const BASE_URL = process.env.AUTH_SMOKE_BASE_URL ?? 'http://localhost/';
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const TIMEOUT_MS = 30_000;

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function authStatus(request) {
  const response = await request.get(new URL('/api/auth/status', BASE_URL).href);
  assert(response.ok(), `GET /api/auth/status -> ${response.status()}`);
  return response.json();
}

async function expectAppLoaded(page) {
  await page.getByTestId('auth-authenticated').waitFor({ timeout: TIMEOUT_MS });
  await page.getByRole('tab', { name: 'Chat' }).waitFor({ timeout: TIMEOUT_MS });
}

async function stepSetupOrLogin(browser, setupRequired) {
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
    if (setupRequired) {
      const code = execFileSync('docker', ['compose', 'exec', '-T', 'platform', 'cat', '/data/platform/setup-code'], {
        cwd: REPO_ROOT,
        encoding: 'utf8',
      }).trim();
      assert(/^[A-Z0-9]{4}(-[A-Z0-9]{4}){3}$/.test(code), 'setup code not readable via docker compose exec');
      await page.getByTestId('auth-setup-screen').waitFor({ timeout: TIMEOUT_MS });
      for (const id of ['auth-setup-code', 'auth-username', 'auth-display-name', 'auth-password', 'auth-password-confirm', 'auth-submit']) {
        assert(await page.getByTestId(id).isVisible(), `setup screen is missing ${id}`);
      }
      assert((await page.getByTestId('auth-authenticated').count()) === 0, 'app rendered before sign-in');
      console.log('  OK setup screen renders (setup code readable; not submitted)');
    } else {
      await page.getByTestId('auth-login-screen').waitFor({ timeout: TIMEOUT_MS });
      console.log('  OK bootstrap done: login screen shows');
    }
  } finally {
    await context.close();
  }
}

async function stepLoginLogout(browser, member) {
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });

    await loginThroughUi(page, { username: member.username, password: 'definitely-wrong' }).then(
      () => {
        throw new Error('login with a wrong password succeeded');
      },
      (error) => assert(/Incorrect username or password/.test(error.message), error.message),
    );
    console.log('  OK wrong password -> "Incorrect username or password."');

    await loginThroughUi(page, member);
    await expectAppLoaded(page);
    const cookies = await context.cookies();
    assert(cookies.some((c) => c.name === 'homeai_session' && c.httpOnly), 'no HttpOnly homeai_session cookie');
    console.log('  OK CLI user signs in -> the app loads (HttpOnly session cookie set)');

    await page.reload({ waitUntil: 'domcontentloaded' });
    await expectAppLoaded(page);
    console.log('  OK reload keeps the session');

    await openSystemApp(page, 'settings');
    const account = await page.getByTestId('settings-account').innerText({ timeout: TIMEOUT_MS });
    assert(account.includes(member.username), `settings shows "${account}"`);
    await page.getByTestId('settings-logout').click();
    await page.locator('[data-testid="auth-login-screen"], [data-testid="auth-setup-screen"]').first().waitFor({ timeout: TIMEOUT_MS });
    assert((await page.getByTestId('auth-authenticated').count()) === 0, 'app still rendered after logout');
    const status = await authStatus(context.request);
    assert(status.authenticated === false, 'session still valid server-side after logout');
    assert(new URL(page.url()).pathname === '/login', `signed out at ${page.url()}, expected /login`);
    await page.goto(new URL('/files', BASE_URL).href, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-testid="auth-login-screen"], [data-testid="auth-setup-screen"]').first().waitFor({ timeout: TIMEOUT_MS });
    assert((await page.getByTestId('auth-authenticated').count()) === 0, '/files rendered while signed out');
    console.log('  OK logout -> /login; session revoked; signed-out /files shows sign-in');
  } finally {
    await context.close();
  }
}

async function acceptInviteInBrowser(browser, token, account) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(new URL(`/invite?token=${encodeURIComponent(token)}`, BASE_URL).href, { waitUntil: 'domcontentloaded' });
  await page.getByTestId('auth-invite-screen').waitFor({ timeout: TIMEOUT_MS });
  await page.getByTestId('auth-username').fill(account.username);
  await page.getByTestId('auth-display-name').fill('E2E Invitee');
  await page.getByTestId('auth-password').fill(account.password);
  await page.getByTestId('auth-password-confirm').fill(account.password);
  await page.getByTestId('auth-submit').click();
  return { context, page };
}

async function stepInvite(browser, admin, invitee) {
  const invite = createInvite(admin);
  try {
    const first = await acceptInviteInBrowser(browser, invite.token, invitee);
    try {
      await expectAppLoaded(first.page);
      assert(/\/(apps)?$/.test(new URL(first.page.url()).pathname), `landed on ${first.page.url()}`);
      const status = await authStatus(first.context.request);
      assert(status.user?.username === invitee.username && status.user?.role === 'member', 'invitee not signed in as a member');
      console.log('  OK /invite?token=… creates a member and lands on Home');
    } finally {
      await first.context.close();
    }

    const reuse = await acceptInviteInBrowser(browser, invite.token, { ...invitee, username: `${invitee.username}x` });
    try {
      const error = await reuse.page.getByTestId('auth-error').innerText({ timeout: TIMEOUT_MS });
      assert(/invite link is invalid/.test(error), `reused invite showed "${error}"`);
      console.log('  OK reusing the invite -> invalid_invite message');
    } finally {
      await reuse.context.close();
    }
  } finally {
    deleteInvite(invite.id);
  }
}

async function main() {
  const startedAt = Date.now();
  const member = createE2eUser({ prefix: 'e2e-auth' });
  const admin = createE2eUser({ prefix: 'e2e-admin', role: 'admin' });
  const invitee = { username: `e2e-inv-${Date.now().toString(36)}`, password: 'e2e-invitee-password' };
  const browser = await chromium.launch({ headless: true });
  try {
    const probe = await browser.newContext();
    const status = await authStatus(probe.request);
    await probe.close();
    console.log(`==> setup_required=${status.setup_required}`);
    await stepSetupOrLogin(browser, status.setup_required);
    await stepLoginLogout(browser, member);
    await stepInvite(browser, admin, invitee);
    console.log(`PASS: auth browser smoke in ${Date.now() - startedAt}ms`);
  } finally {
    await browser.close();
    deleteE2eUsers(member.username, admin.username, invitee.username, `${invitee.username}x`);
  }
}

main().catch((error) => {
  console.error(`FAIL: ${error.stack ?? error}`);
  process.exit(1);
});
