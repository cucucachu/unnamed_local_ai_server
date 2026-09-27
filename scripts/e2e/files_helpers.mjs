// Platform files API helpers for the browser smokes (M11-01). Everything
// goes through Caddy as the signed-in e2e user (the page's own session
// cookie, from `sessionCookie` in auth_helpers.mjs), so seeded files land in
// that user's spaces with the ownership a real upload gets — never by
// writing into host directories.

const API_BASE = process.env.FILES_SMOKE_API_BASE ?? 'http://localhost/api';
const FILES_API = `${API_BASE}/platform/files`;

async function call(cookie, method, url, { json, body, expect = [200] } = {}) {
  const headers = { Cookie: cookie };
  if (json !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(url, {
    method,
    headers,
    body: json !== undefined ? JSON.stringify(json) : body,
  });
  const text = await response.text();
  if (!expect.includes(response.status)) {
    throw new Error(`${method} ${url}: HTTP ${response.status} ${text}`);
  }
  return text ? JSON.parse(text) : null;
}

const withPath = (url, path) => `${url}?${new URLSearchParams({ path })}`;

/** `GET /api/platform/files?path=` — `{ path, entries, role, writable }`, or
 * `{ entries: [], status }` when the server says no. */
export async function listDir(cookie, path) {
  const response = await fetch(withPath(FILES_API, path), { headers: { Cookie: cookie } });
  if (!response.ok) return { entries: [], status: response.status };
  return response.json();
}

export function entryNames(listing) {
  return listing.entries.map((entry) => entry.name);
}

/** Multipart `POST /api/platform/files/upload` of one file into `dir`. */
export async function uploadFile(cookie, dir, name, buffer, mimeType = 'application/octet-stream') {
  const form = new FormData();
  form.append('path', dir);
  form.append('file', new Blob([buffer], { type: mimeType }), name);
  return call(cookie, 'POST', `${FILES_API}/upload`, { body: form, expect: [201] });
}

export async function deleteBestEffort(cookie, path) {
  try {
    await fetch(withPath(FILES_API, path), { method: 'DELETE', headers: { Cookie: cookie } });
  } catch {
    // best effort: already gone, or never created
  }
}

/** A shared space owned by the cookie's user (removed with that user by
 * `deleteE2eUsers`). Returns the space (`id`, `slug`, `name`, ...). */
export async function createSpace(cookie, slug, name) {
  return call(cookie, 'POST', `${API_BASE}/platform/spaces`, { json: { slug, name }, expect: [201] });
}

/** Adds `username` to the space as `role`; the cookie's user must own it. */
export async function addMember(cookie, spaceId, username, role) {
  const directory = await call(cookie, 'GET', `${API_BASE}/platform/users/directory`);
  const user = directory.users.find((u) => u.username === username);
  if (!user) throw new Error(`${username} is not in the user directory`);
  return call(cookie, 'POST', `${API_BASE}/platform/spaces/${spaceId}/members`, {
    json: { user_id: user.id, role },
    expect: [201],
  });
}

/** From anywhere in the Files tab: Home, then into the space shown as `label`
 * ("Personal", or a shared space's name); waits for its breadcrumb. */
export async function openSpace(page, label, timeout = 20_000) {
  const crumbs = page.getByTestId('breadcrumb-segment');
  await crumbs.filter({ hasText: /^Home$/ }).first().click();
  const deadline = Date.now() + timeout;
  while ((await crumbs.count()) !== 1) {
    if (Date.now() > deadline) throw new Error('the Files root never loaded');
    await page.waitForTimeout(100);
  }
  await page.getByText(label, { exact: true }).first().click();
  await page
    .getByTestId('breadcrumb-segment')
    .filter({ hasText: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`) })
    .first()
    .waitFor({ state: 'visible', timeout });
}
