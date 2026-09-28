// M15-04 passkey browser smoke — invoked by `passkey_browser_smoke.sh`.
// Real headless Chromium against Caddy at http://localhost (secure context)
// with a CDP virtual authenticator (ctap2 / internal / residentKey /
// userVerified / automaticPresenceSimulation):
//
//   1. Throwaway e2e-* admin signs in with a password on LAN.
//   2. Settings → Account → add a passkey.
//   3. Sign out, then sign in with that passkey.
//   4. Settings → Users opens the step-up modal; confirm with the passkey.
//
// Never completes bootstrap. The account is deleted on exit.

import { chromium } from 'playwright';

import { createE2eUser, deleteE2eUsers, loginThroughUi } from './auth_helpers.mjs';

const BASE_URL = process.env.PASSKEY_SMOKE_BASE_URL ?? 'http://localhost/';
const TIMEOUT_MS = 30_000;

function byId(page, testID) {
  return page.getByTestId(testID).filter({ visible: true });
}

async function openSettings(page, navTestID) {
  await page.getByRole('tab', { name: 'Settings' }).click();
  await byId(page, navTestID).click();
}

async function attachVirtualAuthenticator(page) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('WebAuthn.enable');
  const { authenticatorId } = await cdp.send('WebAuthn.addVirtualAuthenticator', {
    options: {
      protocol: 'ctap2',
      transport: 'internal',
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  });
  return { cdp, authenticatorId };
}

async function dumpLoginFailure(page, extra) {
  const url = page.url();
  const error = (await page.getByTestId('auth-error').count()) > 0
    ? await page.getByTestId('auth-error').innerText()
    : '(no auth-error)';
  const loginVisible = (await page.getByTestId('auth-login-screen').count()) > 0;
  throw new Error(`${extra} url=${url} loginVisible=${loginVisible} error=${error}`);
}

async function main() {
  const startedAt = Date.now();
  const admin = createE2eUser({ prefix: 'e2e-passkey', role: 'admin' });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
    const { cdp, authenticatorId } = await attachVirtualAuthenticator(page);
    page.on('console', (msg) => {
      if (msg.type() === 'error') console.log(`  console.${msg.type()}: ${msg.text()}`);
    });
    page.on('response', (response) => {
      const url = response.url();
      if (url.includes('/passkey/') && !url.includes('status')) {
        console.log(`  HTTP ${response.status()} ${url.replace(BASE_URL, '/')}`);
      }
    });
    await loginThroughUi(page, admin);
    console.log('  OK password sign-in on LAN');

    await openSettings(page, 'settings-nav-account');
    await byId(page, 'account-passkey-add').waitFor({ timeout: TIMEOUT_MS });
    await byId(page, 'account-passkey-add').click();
    await page.locator('[data-testid^="account-passkey-row-"]').waitFor({ timeout: TIMEOUT_MS });
    const stored = await cdp.send('WebAuthn.getCredentials', { authenticatorId });
    console.log(`  OK added a passkey on Account (${(stored.credentials ?? []).length} authenticator credential(s))`);

    await byId(page, 'settings-back-button').click();
    await byId(page, 'settings-logout').click();
    await page
      .locator('[data-testid="auth-login-screen"], [data-testid="auth-setup-screen"]')
      .first()
      .waitFor({ timeout: TIMEOUT_MS });
    if ((await page.getByTestId('auth-setup-screen').count()) > 0) {
      await page.getByTestId('auth-show-login').click();
    }

    await page.getByTestId('auth-username').fill(admin.username);
    await page.getByTestId('auth-passkey-submit').click();
    await page
      .locator('[data-testid="auth-authenticated"], [data-testid="auth-error"]')
      .first()
      .waitFor({ timeout: TIMEOUT_MS });
    if ((await page.getByTestId('auth-authenticated').count()) === 0) {
      await dumpLoginFailure(page, 'passkey login did not authenticate');
    }
    console.log('  OK signed in with the passkey');

    await openSettings(page, 'settings-nav-users');
    await byId(page, 'step-up-modal').waitFor({ timeout: TIMEOUT_MS });
    await byId(page, 'step-up-passkey').click();
    await byId(page, 'step-up-modal').waitFor({ state: 'detached', timeout: TIMEOUT_MS });
    await byId(page, 'settings-users-screen').waitFor({ timeout: TIMEOUT_MS });
    console.log('  OK admin step-up with the passkey');

    console.log(`PASS: passkey browser smoke in ${Date.now() - startedAt}ms`);
  } finally {
    await browser.close();
    deleteE2eUsers(admin.username);
  }
}

main().catch((error) => {
  console.error(`FAIL: ${error.stack ?? error}`);
  process.exit(1);
});
