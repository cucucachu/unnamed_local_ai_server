// M10-07 Settings/admin smoke — invoked by `admin_browser_smoke.sh`. Real
// headless Chromium against the live stack (Caddy -> platform):
//
//   1. An e2e admin (recovery CLI) signs in, opens Settings -> Invites,
//      confirms the step-up password prompt, and creates an invite; the
//      shown link is built from this origin.
//   2. A second browser context opens that link and creates a member.
//   3. The admin creates a shared space in Settings -> Spaces and adds the
//      new member as an editor from the user directory.
//   4. The member sees the space (role editor) in their Settings -> Spaces,
//      and `/chat/<admin's thread>` or a missing thread shows "Chat not
//      found" (the chat socket's 4404 / history 404).
//   5. A member enrolls TOTP in Settings -> Account (the code is computed
//      here from the shown secret), signs out, and signing back in asks
//      for a code and accepts one.
// Every throwaway account, the space, and the invite are deleted on exit.

import { createHmac, randomBytes } from 'node:crypto';
import { chromium } from 'playwright';

import {
  createE2eUser,
  deleteE2eSpaces,
  deleteE2eUsers,
  deleteInvitesLabeled,
  loginThroughUi,
} from './auth_helpers.mjs';
import { openSystemApp } from './nav_helpers.mjs';

const BASE_URL = process.env.ADMIN_SMOKE_BASE_URL ?? 'http://localhost/';
const TIMEOUT_MS = 30_000;

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

/** Stacked Settings screens stay mounted (hidden) on web, so match only the
 * visible copy of a testID. */
function byId(page, testID) {
  return page.getByTestId(testID).filter({ visible: true });
}

async function openSettings(page, navTestID) {
  await openSystemApp(page, 'settings');
  await byId(page, navTestID).click();
}

// RFC 6238 (SHA-1, 6 digits, 30 s) — mirrors services/platform/app/core/totp.py.
function base32Decode(secret) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const char of secret.replace(/=+$/, '').toUpperCase()) {
    const value = alphabet.indexOf(char);
    assert(value >= 0, `bad base32 character in TOTP secret: ${char}`);
    bits += value.toString(2).padStart(5, '0');
  }
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

function totpAt(secret, step) {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(step));
  const digest = createHmac('sha1', base32Decode(secret)).update(counter).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const value = digest.readUInt32BE(offset) & 0x7fffffff;
  return String(value % 1_000_000).padStart(6, '0');
}

const currentStep = () => Math.floor(Date.now() / 1000 / 30);

async function stepInvite(browser, admin, invitee, inviteLabel) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
  await loginThroughUi(page, admin);
  await openSettings(page, 'settings-nav-invites');

  await byId(page, 'step-up-modal').waitFor({ timeout: TIMEOUT_MS });
  await byId(page, 'step-up-password').fill(admin.password);
  await byId(page, 'step-up-submit').click();
  await byId(page, 'step-up-modal').waitFor({ state: 'detached', timeout: TIMEOUT_MS });
  console.log('  OK admin screens ask for the step-up password, and accept it');

  await byId(page, 'invite-label').fill(inviteLabel);
  await byId(page, 'invite-create').click();
  const link = (await byId(page, 'invite-link').innerText({ timeout: TIMEOUT_MS })).trim();
  const origin = new URL(BASE_URL).origin;
  assert(link.startsWith(`${origin}/invite?token=hi_`), `invite link "${link}" is not under ${origin}`);
  assert(await byId(page, 'invite-qr').isVisible(), 'invite QR code not shown');
  console.log('  OK admin creates an invite (link from this origin + QR)');

  const inviteeContext = await browser.newContext();
  const inviteePage = await inviteeContext.newPage();
  await inviteePage.goto(link, { waitUntil: 'domcontentloaded' });
  await inviteePage.getByTestId('auth-invite-screen').waitFor({ timeout: TIMEOUT_MS });
  await inviteePage.getByTestId('auth-username').fill(invitee.username);
  await inviteePage.getByTestId('auth-display-name').fill('E2E Invitee');
  await inviteePage.getByTestId('auth-password').fill(invitee.password);
  await inviteePage.getByTestId('auth-password-confirm').fill(invitee.password);
  await inviteePage.getByTestId('auth-submit').click();
  await inviteePage.getByTestId('auth-authenticated').waitFor({ timeout: TIMEOUT_MS });
  console.log('  OK a second browser context accepts the invite');

  await byId(page, 'settings-back-button').click();
  await byId(page, 'settings-nav-invites').click();
  await byId(page, 'settings-invites-screen').waitFor({ timeout: TIMEOUT_MS });
  const usedRow = byId(page, 'settings-invites-screen').locator('[data-testid^="invite-row-"]', { hasText: inviteLabel });
  await usedRow.getByText('used', { exact: true }).waitFor({ timeout: TIMEOUT_MS });
  console.log('  OK the invite now lists as used');

  return { admin: { context, page }, invitee: { context: inviteeContext, page: inviteePage } };
}

async function stepSharedSpace({ admin, invitee }, inviteeAccount, space) {
  const { page } = admin;
  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
  await page.getByTestId('auth-authenticated').waitFor({ timeout: TIMEOUT_MS });
  await openSettings(page, 'settings-nav-spaces');
  await byId(page, 'space-create-name').fill(space.name);
  const slug = await byId(page, 'space-create-slug').inputValue();
  assert(slug === space.slug, `derived slug "${slug}", expected "${space.slug}"`);
  await byId(page, 'space-create-submit').click();

  await byId(page, 'settings-space-screen').waitFor({ timeout: TIMEOUT_MS });
  await byId(page, 'space-add-member').click();
  await byId(page, `add-member-user-${inviteeAccount.username}`).click();
  await byId(page, 'add-member-role-editor').click();
  await byId(page, 'add-member-submit').click();
  await byId(page, `member-row-${inviteeAccount.username}`).waitFor({ timeout: TIMEOUT_MS });
  console.log('  OK admin creates a shared space and adds the new member as editor');

  const other = invitee.page;
  await other.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
  await other.getByTestId('auth-authenticated').waitFor({ timeout: TIMEOUT_MS });
  await openSettings(other, 'settings-nav-spaces');
  const row = byId(other, `space-row-${space.slug}`);
  await row.waitFor({ timeout: TIMEOUT_MS });
  const rowText = await row.innerText();
  assert(rowText.includes(space.name) && rowText.includes('editor'), `member's space row reads "${rowText}"`);
  assert(await byId(other, `space-row-${inviteeAccount.username}`).isVisible(), "member's personal space missing");
  await row.click();
  await byId(other, 'settings-space-screen').waitFor({ timeout: TIMEOUT_MS });
  assert((await byId(other, 'space-add-member').count()) === 0, 'an editor was offered member management');
  console.log('  OK the member sees the shared space in Spaces (editor, read-only members)');

  const created = await admin.context.request.post(new URL('/api/threads', BASE_URL).href, { data: { title: null } });
  assert(created.status() === 201, `POST /api/threads -> ${created.status()}`);
  const adminThread = (await created.json()).id;
  for (const threadId of [adminThread, '00000000-0000-4000-8000-000000000000']) {
    await other.goto(new URL(`/chat/${threadId}`, BASE_URL).href, { waitUntil: 'domcontentloaded' });
    await byId(other, 'chat-not-found').waitFor({ timeout: TIMEOUT_MS });
  }
  await byId(other, 'chat-not-found-back').click();
  await byId(other, 'new-chat-header-button').waitFor({ timeout: TIMEOUT_MS });
  console.log("  OK another user's thread and a missing one show \"Chat not found\" (no reconnect loop)");
}

async function stepTotp(browser, account) {
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
    await loginThroughUi(page, account);
    await openSettings(page, 'settings-nav-account');

    await byId(page, 'account-totp-enable').click();
    await byId(page, 'account-totp-password').fill(account.password);
    await byId(page, 'account-totp-continue').click();
    const secret = (await byId(page, 'account-totp-secret').innerText({ timeout: TIMEOUT_MS })).trim();
    const uri = (await byId(page, 'account-totp-uri').innerText()).trim();
    assert(uri.startsWith(`otpauth://totp/HomeAI:${account.username}?secret=${secret}`), `otpauth URI "${uri}"`);
    assert(await byId(page, 'account-totp-qr').isVisible(), 'TOTP QR code not shown');

    const confirmStep = currentStep();
    await byId(page, 'account-totp-code').fill(totpAt(secret, confirmStep));
    await byId(page, 'account-totp-confirm').click();
    await byId(page, 'account-totp-status').getByText('On', { exact: true }).waitFor({ timeout: TIMEOUT_MS });
    console.log('  OK TOTP enrollment: QR + secret shown, computed code confirms it');

    await byId(page, 'settings-back-button').click();
    await byId(page, 'settings-logout').click();
    await page.locator('[data-testid="auth-login-screen"], [data-testid="auth-setup-screen"]').first().waitFor({ timeout: TIMEOUT_MS });
    if ((await page.getByTestId('auth-setup-screen').count()) > 0) await page.getByTestId('auth-show-login').click();

    await page.getByTestId('auth-username').fill(account.username);
    await page.getByTestId('auth-password').fill(account.password);
    await page.getByTestId('auth-submit').click();
    await page.getByTestId('auth-totp').waitFor({ timeout: TIMEOUT_MS });
    const prompt = await page.getByTestId('auth-error').innerText();
    assert(/6-digit code/.test(prompt), `password-only sign-in showed "${prompt}"`);
    assert((await page.getByTestId('auth-authenticated').count()) === 0, 'signed in without a TOTP code');

    // Each step's code works once; the platform accepts one step of drift.
    const step = Math.max(currentStep(), confirmStep + 1);
    await page.getByTestId('auth-totp').fill(totpAt(secret, step));
    await page.getByTestId('auth-submit').click();
    await page.getByTestId('auth-authenticated').waitFor({ timeout: TIMEOUT_MS });
    console.log('  OK after sign-out, sign-in requires the code and accepts it');
  } finally {
    await context.close();
  }
}

async function main() {
  const startedAt = Date.now();
  const suffix = randomBytes(3).toString('hex');
  const admin = createE2eUser({ prefix: 'e2e-admin', role: 'admin' });
  const totpUser = createE2eUser({ prefix: 'e2e-totp' });
  const invitee = { username: `e2e-inv-${suffix}`, password: randomBytes(12).toString('hex') };
  const inviteLabel = `e2e-admin-smoke ${suffix}`;
  const space = { name: `E2E Space ${suffix}`, slug: `e2e-space-${suffix}` };
  const browser = await chromium.launch({ headless: true });
  try {
    const contexts = await stepInvite(browser, admin, invitee, inviteLabel);
    try {
      await stepSharedSpace(contexts, invitee, space);
    } finally {
      await contexts.admin.context.close();
      await contexts.invitee.context.close();
    }
    await stepTotp(browser, totpUser);
    console.log(`PASS: admin browser smoke in ${Date.now() - startedAt}ms`);
  } finally {
    await browser.close();
    deleteE2eSpaces(space.slug);
    deleteInvitesLabeled(inviteLabel);
    deleteE2eUsers(admin.username, totpUser.username, invitee.username);
  }
}

main().catch((error) => {
  console.error(`FAIL: ${error.stack ?? error}`);
  process.exit(1);
});
