// #483: the Stack template in the browser. Driven by stack_smoke.sh (which
// makes the users and cleans up); see its header for what is checked.
//
// Env: STACK_SMOKE_USER / _EDITOR / _VIEWER and their _PASSWORDs (e2e-*
// users), STACK_SMOKE_SPACE_SLUG (e2e-stack-*), STACK_SMOKE_STATE (a file this
// writes the app id to, for the cleanup trap), STACK_SMOKE_BASE_URL (default
// http://localhost/), STACK_SMOKE_SCREENSHOTS (optional: a folder for the
// game-over screens and the leaderboard).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

import { loginThroughUi } from './auth_helpers.mjs';

const BASE = (process.env.STACK_SMOKE_BASE_URL ?? 'http://localhost/').replace(/\/$/, '');
const OWNER = { username: process.env.STACK_SMOKE_USER, password: process.env.STACK_SMOKE_PASSWORD };
const EDITOR = { username: process.env.STACK_SMOKE_EDITOR, password: process.env.STACK_SMOKE_EDITOR_PASSWORD };
const VIEWER = { username: process.env.STACK_SMOKE_VIEWER, password: process.env.STACK_SMOKE_VIEWER_PASSWORD };
const SLUG = process.env.STACK_SMOKE_SPACE_SLUG;
const STATE = process.env.STACK_SMOKE_STATE;
if (![OWNER, EDITOR, VIEWER].every((u) => /^e2e-/.test(u.username ?? '') && u.password) || !/^e2e-stack-/.test(SLUG ?? '') || !STATE) {
  throw new Error('STACK_SMOKE_USER / _EDITOR / _VIEWER (e2e-*), their passwords, STACK_SMOKE_SPACE_SLUG (e2e-stack-*) and STACK_SMOKE_STATE are required');
}
const UI_TIMEOUT = 30_000;
const LIVE_TIMEOUT = 10_000;
const BUILD_TIMEOUT = 240_000;
const APP_FRAME = 'iframe[title="app"]';
const SHOTS = process.env.STACK_SMOKE_SCREENSHOTS;

const ok = (what) => console.log(`ok   ${what}`);
function check(cond, what, detail) {
  if (!cond) throw new Error(`FAIL: ${what}${detail === undefined ? '' : `: ${JSON.stringify(detail)}`}`);
  ok(what);
}

async function until(what, probe, timeout = UI_TIMEOUT) {
  const started = Date.now();
  let detail;
  while (Date.now() - started < timeout) {
    let done;
    try {
      [done, detail] = await probe();
    } catch (e) {
      [done, detail] = [false, e.message];
    }
    if (done) {
      const ms = Date.now() - started;
      ok(`${what} (${ms} ms)`);
      return ms;
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`FAIL: ${what}: ${JSON.stringify(detail)}`);
}

function api(context) {
  return async (method, url, { json, timeout = 30_000, allow = [] } = {}) => {
    const r = await context.request.fetch(`${BASE}${url}`, { method, timeout, ...(json !== undefined ? { data: json } : {}) });
    const text = await r.text();
    if (!r.ok() && !allow.includes(r.status())) throw new Error(`${method} ${url}: ${r.status()} ${text}`);
    return { status: r.status(), body: text ? JSON.parse(text) : null };
  };
}

async function signIn(browser, user, colorScheme = 'light') {
  const context = await browser.newContext({ viewport: { width: 600, height: 900 }, colorScheme });
  const page = await context.newPage();
  await page.goto(`${BASE}/`);
  await loginThroughUi(page, user);
  const call = api(context);
  return { context, page, call, json: async (...args) => (await call(...args)).body };
}

const ui = (page) => page.frameLocator(APP_FRAME);
const el = (page, testID) => ui(page).getByTestId(testID);
const shown = async (page, testID) => (await el(page, testID).count()) > 0;
const text = (page, testID) => el(page, testID).innerText({ timeout: 2000 });
const lines = (s) => s.split('\n').map((l) => l.trim());
const sleep = (ms) => new Promise((r) => setTimeout(r, Math.max(0, ms)));

async function openApp(page, instance, readyTestID) {
  await page.goto(`${BASE}/apps/${instance.id}`);
  await page.getByTestId('app-runner').waitFor({ timeout: UI_TIMEOUT });
  await el(page, readyTestID).waitFor({ timeout: UI_TIMEOUT });
}

// --- the game's own timing, from the template ----------------------------------
// crossingSeconds(score) = Math.max(MIN, START - score * STEP): a block is over the tower half way across.
const STACK_TS = fs.readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../examples/apps/stack/lib/stack.ts'), 'utf8');
const [, MIN, START, STEP] = STACK_TS.match(/return Math\.max\(([\d.]+), ([\d.]+) - score \* ([\d.]+)\);/).map(Number);
const overTheTowerMs = (score) => (Math.max(MIN, START - score * STEP) * 1000) / 2;

/** A tap on the game (onPressIn: mouse down is the drop), at the stage's centre; returns when it was sent. */
async function tap(page) {
  const box = await el(page, 'stage').boundingBox();
  const at = Date.now();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.up();
  return at;
}

const browser = await chromium.launch();
try {
  const owner = await signIn(browser, OWNER);
  ok(`signed in as ${OWNER.username}`);

  // --- setup over REST ----------------------------------------------------------------
  const shared = await owner.json('POST', '/api/platform/spaces', { json: { slug: SLUG, name: `E2E Stack ${process.pid}` } });
  const directory = (await owner.json('GET', '/api/platform/users/directory')).users;
  const user = (u) => directory.find((d) => d.username === u.username);
  await owner.json('POST', `/api/platform/spaces/${shared.id}/members`, { json: { user_id: user(EDITOR).id, role: 'editor' } });
  await owner.json('POST', `/api/platform/spaces/${shared.id}/members`, { json: { user_id: user(VIEWER).id, role: 'viewer' } });
  const out = await owner.json('POST', `/api/platform/spaces/${shared.id}/app-templates/stack/install`, { json: {}, timeout: BUILD_TIMEOUT });
  fs.appendFileSync(STATE, `${out.app.id}\n`);
  check(out.build?.ok && out.build.diagnostics.length === 0, 'installed and built Stack with zero diagnostics', out.build);
  const instance = out.instance;
  const rpc = (who, body) => who.call('POST', `/api/platform/apps/instances/${instance.id}/rpc`, { json: body, allow: [403] });
  const games = async () => (await rpc(owner, { op: 'getAll', sql: 'SELECT score, perfects, _created_by FROM games ORDER BY id', params: [] })).body.rows;

  const editor = await signIn(browser, EDITOR, 'dark');
  const viewer = await signIn(browser, VIEWER);
  ok('signed in as the editor (dark mode) and the viewer');
  const { page } = owner;
  const three = page.waitForResponse((r) => r.url().endsWith('/app-runtime/1/three.js') && r.status() === 200, { timeout: UI_TIMEOUT });
  await openApp(page, instance, 'start');
  await three;
  ok('the host fetched three.js for it');
  await ui(page).locator('canvas').waitFor({ timeout: UI_TIMEOUT });
  check(!(await ui(page).getByText("3D graphics aren't available", { exact: false }).count()), 'Stack opens on "Tap to start" with a WebGL canvas');

  // --- the owner stacks 3, then misses -----------------------------------------------------
  let last = await tap(page);
  await until('tapping starts a game at 0', async () => [(await text(page, 'score')) === '0', await text(page, 'score')]);
  const perfect = [];
  for (let score = 0; score < 3; score++) {
    await sleep(last + overTheTowerMs(score) - Date.now());
    last = await tap(page);
    await until(`a tap with the block over the tower stacks block ${score + 1}`, async () => [(await text(page, 'score')) === String(score + 1), await text(page, 'score')], 3000);
    perfect.push(await shown(page, 'perfect'));
  }
  console.log(`     perfect drops: ${perfect.map((p) => (p ? 'yes' : 'no')).join(', ')}`);
  await sleep(last + 40 - Date.now());
  await tap(page);
  await el(page, 'game-over').waitFor({ timeout: 3000 });
  check((await text(page, 'over-title')) === 'New best!', 'a tap as the next block appears misses: game over, a new best');
  await until('the game is saved as one row, score 3, stamped with the owner', async () => {
    const g = await games();
    return [g.length === 1 && g[0].score === 3 && g[0]._created_by === user(OWNER).id && g[0].perfects === perfect.filter(Boolean).length, g];
  });
  check(!(await page.getByTestId('app-error-overlay').count()), 'no app errors');
  if (SHOTS) {
    await sleep(800);
    await page.screenshot({ path: `${SHOTS}/stack-owner-over.png` });
  }

  // --- the editor misses at once; the owner's leaderboard shows it live ----------------
  await el(page, 'tab-scores').click();
  await el(page, `leader-${OWNER.username}`).waitFor({ timeout: UI_TIMEOUT });
  check((await text(page, 'stat-best')) === '3' && (await text(page, 'stat-games')) === '1', 'the owner’s Scores: best 3, 1 game');
  check(!(await shown(page, `leader-${EDITOR.username}`)), 'the editor isn’t on the leaderboard before playing');
  await openApp(editor.page, instance, 'start');
  const startAt = await tap(editor.page);
  await until('the editor’s game starts', async () => [(await text(editor.page, 'score')) === '0', null], 3000);
  await sleep(startAt + 40 - Date.now());
  await tap(editor.page);
  await el(editor.page, 'game-over').waitFor({ timeout: 3000 });
  const live = await until('the owner’s leaderboard shows the editor’s game without reloading', async () => [(await shown(page, `leader-${EDITOR.username}`)) && lines(await text(page, `leader-${EDITOR.username}`)).includes('0'), null], LIVE_TIMEOUT);
  console.log(`     cross-member refresh: ${live} ms`);
  const order = await ui(page).locator('[data-testid^="leader-"]').evaluateAll((els) => els.map((e) => e.getAttribute('data-testid')));
  check(JSON.stringify(order) === JSON.stringify([`leader-${OWNER.username}`, `leader-${EDITOR.username}`]), 'ranked by best: the owner (3), then the editor (0)', order);
  if (SHOTS) {
    await editor.page.screenshot({ path: `${SHOTS}/stack-editor-dark.png` });
    await page.screenshot({ path: `${SHOTS}/stack-leaderboard.png` });
  }

  // --- the owner's second game -----------------------------------------------------------
  await el(page, 'tab-index').click();
  await el(page, 'stage').waitFor({ timeout: UI_TIMEOUT });
  last = await tap(page);
  await until('a tap on the game-over screen starts again at 0', async () => [(await text(page, 'score')) === '0', await text(page, 'score')], 3000);
  await sleep(last + 40 - Date.now());
  await tap(page);
  await until('missing again: "Game over", best still 3', async () => [(await text(page, 'over-title')) === 'Game over' && (await el(page, 'game-over').innerText()).includes('Your best: 3'), await el(page, 'game-over').innerText()], 3000);
  await until('three games saved, each stamped with its player', async () => {
    const g = await games();
    return [JSON.stringify(g.map((r) => [r.score, r._created_by])) === JSON.stringify([[3, user(OWNER).id], [0, user(EDITOR).id], [0, user(OWNER).id]]), g];
  });
  await el(page, 'tab-scores').click();
  await until('the owner’s Scores: 2 games, and the newest first in recent games', async () => [(await text(page, 'stat-games')) === '2' && lines(await text(page, 'recent-0')).includes('0 blocks') && lines(await text(page, 'recent-1')).includes('3 blocks'), null]);

  // --- the viewer -----------------------------------------------------------------------
  await openApp(viewer.page, instance, 'viewer-note');
  await tap(viewer.page);
  await sleep(300);
  check((await text(viewer.page, 'score')) === '' && !(await shown(viewer.page, 'game-over')), 'the viewer is told they can’t play, and a tap does nothing');
  await el(viewer.page, 'tab-scores').click();
  await el(viewer.page, `leader-${EDITOR.username}`).waitFor({ timeout: UI_TIMEOUT });
  check(!(await shown(viewer.page, `leader-${VIEWER.username}`)), 'the viewer sees the leaderboard, without themselves on it');
  const refused = await rpc(viewer, { op: 'run', sql: 'INSERT INTO games (score) VALUES (99)', params: [] });
  check(refused.status === 403 && (await games()).length === 3, 'a write as the viewer is refused', refused.status);

  await viewer.context.close();
  await editor.context.close();
  await owner.context.close();
  console.log('PASS: stack smoke');
} finally {
  await browser.close();
}
