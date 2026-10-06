// Shared sign-in plumbing for the browser smokes (M10-06). The web app now
// opens on Setup/Login until it has a session, so every smoke creates a
// throwaway `e2e-*` account with the platform's recovery CLI, signs in
// through the real login form, and deletes the account when it's done.
//
// Never completes bootstrap: when the server still wants setup, the login
// helper switches to the sign-in form ("Already have an account?") instead
// of submitting the setup code — the maintainer must be the first admin.

import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

function compose(args, input) {
  return execFileSync('docker', ['compose', ...args], {
    cwd: REPO_ROOT,
    input,
    encoding: 'utf8',
    stdio: ['pipe', 'pipe', 'pipe'],
  });
}

function envValue(name, fallback) {
  const envFile = path.join(REPO_ROOT, '.env');
  if (!existsSync(envFile)) return fallback;
  const match = readFileSync(envFile, 'utf8').match(new RegExp(`^${name}=(.*)$`, 'm'));
  return match ? match[1].trim() : fallback;
}

function psql(sql, db = 'homeai_platform') {
  return compose(
    ['exec', '-T', 'postgres', 'psql', '-qtA', '-v', 'ON_ERROR_STOP=1', '-U', envValue('POSTGRES_USER', 'homeai'),
      '-d', db, '-c', sql],
    '',
  ).trim();
}

/** `homeai_session=...` for this browser context's signed-in user, as a
 * `Cookie` header value — for the smokes' side-channel REST calls, which
 * must act as the same user as the page (threads and settings are per user). */
export async function sessionCookie(context) {
  const cookie = (await context.cookies()).find((c) => c.name === 'homeai_session');
  if (!cookie) throw new Error('no homeai_session cookie in this browser context');
  return `homeai_session=${cookie.value}`;
}

/** Creates `<prefix>-<random>` via the recovery CLI; returns its credentials. */
export function createE2eUser({ prefix = 'e2e-ui', role = 'member' } = {}) {
  const username = `${prefix}-${randomBytes(4).toString('hex')}`;
  const password = randomBytes(16).toString('hex');
  compose(
    ['exec', '-T', 'platform', 'python', '-m', 'app.cli', 'create-user', username,
      '--display-name', `E2E ${prefix}`, '--role', role, '--password-stdin'],
    `${password}\n`,
  );
  return { username, password };
}

/** Deletes throwaway users (sessions cascade), their agent-server threads,
 * checkpoints, and settings, and, where the platform has spaces, their
 * personal space row and directory. Best effort: cleanup must not mask the
 * real failure. */
export function deleteE2eUsers(...usernames) {
  const names = usernames.filter((name) => /^e2e-[a-z0-9._-]+$/.test(name ?? ''));
  if (names.length === 0) return;
  const list = names.map((n) => `'${n}'`).join(', ');
  try {
    const userIds = psql(`SELECT id FROM users WHERE username IN (${list})`)
      .split('\n').filter((id) => /^[0-9a-f-]{36}$/.test(id));
    if (userIds.length > 0) {
      const ids = userIds.map((id) => `'${id}'`).join(', ');
      psql(`
        CREATE TEMP TABLE doomed AS SELECT id::text AS id FROM threads WHERE owner_user_id IN (${ids});
        DELETE FROM checkpoint_writes WHERE thread_id IN (SELECT id FROM doomed);
        DELETE FROM checkpoint_blobs WHERE thread_id IN (SELECT id FROM doomed);
        DELETE FROM checkpoints WHERE thread_id IN (SELECT id FROM doomed);
        DELETE FROM turn_stats WHERE thread_id IN (SELECT id FROM doomed);
        DELETE FROM threads WHERE owner_user_id IN (${ids});
        DELETE FROM user_settings WHERE user_id IN (${ids});`, envValue('POSTGRES_DB', 'homeai'));
    }
    let spaceIds = [];
    if (psql("SELECT to_regclass('public.spaces') IS NOT NULL") === 't') {
      spaceIds = psql(
        `SELECT s.id FROM spaces s JOIN users u ON u.id = s.owner_user_id WHERE u.username IN (${list})`,
      ).split('\n').filter((id) => /^[0-9a-f-]{36}$/.test(id));
      psql(`DELETE FROM spaces s USING users u WHERE u.id = s.owner_user_id AND u.username IN (${list})`);
    }
    psql(`DELETE FROM users WHERE username IN (${list})`);
    for (const id of spaceIds) compose(['exec', '-T', 'platform', 'rm', '-rf', `/data/spaces/${id}`], '');
  } catch (error) {
    console.warn(`WARN: could not delete ${names.join(', ')}: ${error.message}`);
  }
}

/** Deletes shared spaces by slug (memberships cascade) and their
 * directories. Only `e2e-*` slugs; best effort. */
export function deleteE2eSpaces(...slugs) {
  const names = slugs.filter((slug) => /^e2e-[a-z0-9-]+$/.test(slug ?? ''));
  if (names.length === 0) return;
  const list = names.map((n) => `'${n}'`).join(', ');
  try {
    const ids = psql(`DELETE FROM spaces WHERE slug IN (${list}) AND kind = 'shared' RETURNING id`)
      .split('\n').filter((id) => /^[0-9a-f-]{36}$/.test(id));
    for (const id of ids) compose(['exec', '-T', 'platform', 'rm', '-rf', `/data/spaces/${id}`], '');
  } catch (error) {
    console.warn(`WARN: could not delete spaces ${names.join(', ')}: ${error.message}`);
  }
}

/** Removes registered apps' bundles and git repos (their rows cascade from
 * the space `deleteE2eSpaces` deletes). */
export function deleteE2eAppFiles(...appIds) {
  for (const id of appIds.filter((appId) => /^[0-9a-f-]{36}$/.test(appId ?? ''))) {
    try {
      compose(['exec', '-T', 'platform', 'rm', '-rf', `/data/platform/app-bundles/${id}`, `/data/platform/app-git/${id}.git`], '');
    } catch (error) {
      console.warn(`WARN: could not delete app files for ${id}: ${error.message}`);
    }
  }
}

/** Deletes invites carrying `label` (an `e2e-*` label from a UI smoke,
 * which never sees the invite id). */
export function deleteInvitesLabeled(label) {
  if (!/^e2e-[a-z0-9 -]+$/.test(label ?? '')) return;
  try {
    psql(`DELETE FROM invites WHERE label = '${label}'`);
  } catch (error) {
    console.warn(`WARN: could not delete invites labeled ${label}: ${error.message}`);
  }
}

export function deleteInvite(inviteId) {
  if (!/^[0-9a-f-]{36}$/.test(inviteId ?? '')) return;
  try {
    psql(`DELETE FROM invites WHERE id = '${inviteId}'`);
  } catch (error) {
    console.warn(`WARN: could not delete invite ${inviteId}: ${error.message}`);
  }
}

// Runs inside the platform container and plays Caddy's part (verify ->
// identity header) the same way platform_auth_smoke.sh does, with a native
// bearer session rather than a cookie.
const CREATE_INVITE_PY = `
import json, sys, urllib.request
username, password = sys.stdin.read().split("\\n")[:2]
base = "http://localhost:8100"
def call(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
        return resp.headers, (json.loads(raw) if raw else None)
_, login = call("POST", "/api/auth/login", {"username": username, "password": password, "device_label": "e2e invite"},
                {"X-HomeAI-Client": "native"})
bearer = {"Authorization": "Bearer " + login["session_token"]}
call("POST", "/api/auth/step-up", {"password": password}, bearer)
headers, _ = call("GET", "/internal/auth/verify", None, bearer)
_, invite = call("POST", "/api/platform/admin/invites", {"label": "e2e browser smoke"},
                 {"X-HomeAI-Identity": headers["X-HomeAI-Identity"]})
call("POST", "/api/auth/logout", None, bearer)
print(json.dumps({"id": invite["id"], "token": invite["token"]}))
`;

/** Creates a single-use invite as `admin` (an `e2e-*` admin from
 * `createE2eUser({ role: 'admin' })`). Returns `{ id, token }`. */
export function createInvite(admin) {
  const out = compose(
    ['exec', '-T', 'platform', 'python', '-c', CREATE_INVITE_PY],
    `${admin.username}\n${admin.password}\n`,
  );
  return JSON.parse(out.trim().split('\n').pop());
}

const AUTH_OR_APP =
  '[data-testid="auth-username"], [data-testid="auth-setup-code"], [data-testid="auth-authenticated"]';

/** Signs in through the web login form if the page is showing Setup/Login;
 * a no-op if this browser context already has a session. Signed-out visits
 * land on `/login` and signing in lands on Chat, so navigate to a deep URL
 * only after this. */
export async function loginThroughUi(page, { username, password }, { timeout = 30_000 } = {}) {
  await page.locator(AUTH_OR_APP).first().waitFor({ timeout });
  if ((await page.getByTestId('auth-authenticated').count()) > 0) return;

  if ((await page.getByTestId('auth-setup-code').count()) > 0) {
    await page.getByTestId('auth-show-login').click();
  }
  await page.getByTestId('auth-username').fill(username);
  await page.getByTestId('auth-password').fill(password);
  const staleError = (await page.getByTestId('auth-error').count()) > 0;
  await page.getByTestId('auth-submit').click();
  if (staleError) await page.getByTestId('auth-error').waitFor({ state: 'detached', timeout });

  await page
    .locator('[data-testid="auth-authenticated"], [data-testid="auth-error"]')
    .first()
    .waitFor({ timeout });
  if ((await page.getByTestId('auth-error').count()) > 0) {
    throw new Error(`login as ${username} failed: ${await page.getByTestId('auth-error').innerText()}`);
  }
}
