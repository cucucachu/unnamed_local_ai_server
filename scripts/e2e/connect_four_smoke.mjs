// #481: the Connect Four template in the browser. Driven by connect_four_smoke.sh
// (which makes the users and cleans up); see its header for what is checked.
//
// Env: C4_SMOKE_USER / _EDITOR / _VIEWER and their _PASSWORDs (e2e-* users),
// C4_SMOKE_SPACE_SLUG (e2e-c4-*), C4_SMOKE_STATE (a file this writes the app
// id to, for the cleanup trap), C4_SMOKE_BASE_URL (default http://localhost/),
// C4_SMOKE_SCREENSHOTS (optional: a folder for both players' boards).
import fs from 'node:fs';
import { chromium } from 'playwright';

import { loginThroughUi } from './auth_helpers.mjs';

const BASE = (process.env.C4_SMOKE_BASE_URL ?? 'http://localhost/').replace(/\/$/, '');
const OWNER = { username: process.env.C4_SMOKE_USER, password: process.env.C4_SMOKE_PASSWORD };
const EDITOR = { username: process.env.C4_SMOKE_EDITOR, password: process.env.C4_SMOKE_EDITOR_PASSWORD };
const VIEWER = { username: process.env.C4_SMOKE_VIEWER, password: process.env.C4_SMOKE_VIEWER_PASSWORD };
const SLUG = process.env.C4_SMOKE_SPACE_SLUG;
const STATE = process.env.C4_SMOKE_STATE;
if (![OWNER, EDITOR, VIEWER].every((u) => /^e2e-/.test(u.username ?? '') && u.password) || !/^e2e-c4-/.test(SLUG ?? '') || !STATE) {
  throw new Error('C4_SMOKE_USER / _EDITOR / _VIEWER (e2e-*), their passwords, C4_SMOKE_SPACE_SLUG (e2e-c4-*) and C4_SMOKE_STATE are required');
}
const UI_TIMEOUT = 30_000;
const LIVE_TIMEOUT = 10_000;
const BUILD_TIMEOUT = 240_000;
const APP_FRAME = 'iframe[title="app"]';
const SHOTS = process.env.C4_SMOKE_SCREENSHOTS;

const ok = (what) => console.log(`ok   ${what}`);
function check(cond, what, detail) {
  if (!cond) throw new Error(`FAIL: ${what}${detail === undefined ? '' : `: ${JSON.stringify(detail)}`}`);
  ok(what);
}

async function until(what, probe, timeout = UI_TIMEOUT, quiet = false) {
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
      if (!quiet) ok(`${what} (${ms} ms)`);
      return ms;
    }
    await new Promise((r) => setTimeout(r, 50));
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

async function signIn(browser, user) {
  const context = await browser.newContext({ viewport: { width: 600, height: 900 } });
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
const cell = (page, c, r) => el(page, `cell-${c}-${r}`).getAttribute('aria-label', { timeout: 2000 });

async function openApp(page, instance, readyTestID) {
  await page.goto(`${BASE}/apps/${instance.id}`);
  await page.getByTestId('app-runner').waitFor({ timeout: UI_TIMEOUT });
  await el(page, readyTestID).waitFor({ timeout: UI_TIMEOUT });
}

const browser = await chromium.launch();
try {
  const owner = await signIn(browser, OWNER);
  ok(`signed in as ${OWNER.username}`);

  // --- setup over REST ----------------------------------------------------------------
  const shared = await owner.json('POST', '/api/platform/spaces', { json: { slug: SLUG, name: `E2E Connect Four ${process.pid}` } });
  const directory = (await owner.json('GET', '/api/platform/users/directory')).users;
  const user = (u) => directory.find((d) => d.username === u.username);
  await owner.json('POST', `/api/platform/spaces/${shared.id}/members`, { json: { user_id: user(EDITOR).id, role: 'editor' } });
  await owner.json('POST', `/api/platform/spaces/${shared.id}/members`, { json: { user_id: user(VIEWER).id, role: 'viewer' } });
  const out = await owner.json('POST', `/api/platform/spaces/${shared.id}/app-templates/connect-four/install`, { json: {}, timeout: BUILD_TIMEOUT });
  fs.appendFileSync(STATE, `${out.app.id}\n`);
  check(out.build?.ok && out.build.diagnostics.length === 0, 'installed and built Connect Four with zero diagnostics', out.build);
  const instance = out.instance;
  const rpc = (who, body) => who.call('POST', `/api/platform/apps/instances/${instance.id}/rpc`, { json: body, allow: [403] });
  const rows = async (sql, params = []) => (await rpc(owner, { op: 'getAll', sql, params })).body.rows;
  const games = () => rows('SELECT id, red, yellow, status, winner, _created_by FROM games ORDER BY id');
  const moves = (gameId) => rows('SELECT turn, col, row, _created_by FROM moves WHERE game_id = ? ORDER BY turn', [gameId]);

  const editor = await signIn(browser, EDITOR);
  const viewer = await signIn(browser, VIEWER);
  ok('signed in as the editor and the viewer');
  const { page } = owner;
  const ownerName = user(OWNER).display_name;

  // --- a game: owner (red, first) against the editor ---------------------------------
  await openApp(page, instance, 'games-empty');
  await el(page, 'new-game').click();
  await el(page, `opponent-${EDITOR.username}`).click();
  await el(page, 'first-me').click();
  await el(page, 'start').click();
  await el(page, 'board').waitFor({ timeout: UI_TIMEOUT });
  const [g1] = await games();
  check(g1.red === user(OWNER).id && g1.yellow === user(EDITOR).id && g1.status === 'playing', 'a new game: the owner is red and moves first', g1);
  check((await text(page, 'status')) === 'Your turn', 'the owner sees "Your turn"');

  await openApp(editor.page, instance, `game-${g1.id}`);
  check(await shown(editor.page, 'their-turn'), 'the editor’s list has it under "Their turn"');
  await el(editor.page, `game-${g1.id}`).click();
  await el(editor.page, 'board').waitFor({ timeout: UI_TIMEOUT });
  check((await text(editor.page, 'status')) === `Waiting for ${ownerName}`, 'the editor is waiting for the owner');
  await el(editor.page, 'col-3').click({ force: true });
  await new Promise((r) => setTimeout(r, 300));
  check((await moves(g1.id)).length === 0, 'tapping the board out of turn does nothing');

  // Red stacks column 0, yellow column 1; red's fourth wins.
  const plan = [0, 1, 0, 1, 0, 1, 0];
  const times = [];
  for (let turn = 0; turn < plan.length; turn++) {
    const [mover, other] = turn % 2 === 0 ? [page, editor.page] : [editor.page, page];
    const color = turn % 2 === 0 ? 'red' : 'yellow';
    const row = Math.floor(turn / 2);
    await until(`turn ${turn + 1}: the mover's board is ready`, async () => [(await text(mover, 'status')) === 'Your turn' || turn === plan.length, null], UI_TIMEOUT, true);
    await el(mover, `col-${plan[turn]}`).click();
    times.push(await until(`move ${turn + 1}`, async () => [(await cell(other, plan[turn], row)) === color, await cell(other, plan[turn], row)], LIVE_TIMEOUT, true));
  }
  ok(`seven moves, each on the other player's board without reloading (${Math.min(...times)}–${Math.max(...times)} ms)`);
  await until('the owner wins with four in the first column', async () => [(await text(page, 'status')) === 'You won!' && (await text(editor.page, 'status')) === `${ownerName} won`, [await text(page, 'status'), await text(editor.page, 'status')]]);
  const won = (await games())[0];
  check(won.status === 'won' && won.winner === user(OWNER).id, 'the game is stored as won by the owner', won);
  const ms = await moves(g1.id);
  check(ms.length === 7 && ms.every((m) => m._created_by === (m.turn % 2 === 0 ? user(OWNER).id : user(EDITOR).id)), 'seven moves, each stamped with the player whose turn it was');
  check(!(await shown(page, 'out-of-turn')), 'and no out-of-turn warning');
  if (SHOTS) {
    await page.screenshot({ path: `${SHOTS}/c4-owner.png` });
    await editor.page.screenshot({ path: `${SHOTS}/c4-editor.png` });
  }

  // --- rematch: colors swap -------------------------------------------------------------
  await el(editor.page, 'rematch').click();
  await until('Rematch starts a new game with the editor as red', async () => {
    const gs = await games();
    return [gs.length === 2 && gs[1].red === user(EDITOR).id && gs[1].yellow === user(OWNER).id, gs];
  });
  const g2 = (await games())[1];
  await el(editor.page, 'board').waitFor({ timeout: UI_TIMEOUT });
  await openApp(page, instance, `game-${g2.id}`);
  await el(page, `game-${g2.id}`).click();
  await el(page, 'board').waitFor({ timeout: UI_TIMEOUT });
  // Fill column 3, alternating: no four.
  for (let turn = 0; turn < 6; turn++) {
    const [mover, other] = turn % 2 === 0 ? [editor.page, page] : [page, editor.page];
    await until('mover ready', async () => [(await text(mover, 'status')) === 'Your turn', await text(mover, 'status')], UI_TIMEOUT, true);
    await el(mover, 'col-3').click();
    await until('landed', async () => [(await cell(other, 3, turn)) === (turn % 2 === 0 ? 'red' : 'yellow'), null], LIVE_TIMEOUT, true);
  }
  ok('column 3 filled, alternating colors');
  await until('the editor to move', async () => [(await text(editor.page, 'status')) === 'Your turn', null]);
  await el(editor.page, 'col-3').click();
  check((await text(editor.page, 'problem')) === 'That column is full.', 'a full column is refused');
  check((await moves(g2.id)).length === 6, 'and nothing was stored');

  // --- turns are the screen's: a move made around it is pointed out ---------------------
  const sneak = (await rpc(owner, { op: 'action', name: 'dropDisc', params: { game_id: g2.id, turn: 6, col: 0, result: 'playing', winner: null } })).body;
  const again = (await rpc(owner, { op: 'action', name: 'dropDisc', params: { game_id: g2.id, turn: 6, col: 0, result: 'playing', winner: null } })).body;
  check(sneak.rows[0].moves === 7 && again.rows[0].moves === 7, 'dropDisc takes only the next move: the same move twice lands once', [sneak.rows, again.rows]);
  await until('the editor’s screen says the owner moved in the editor’s turn', async () => {
    const t = await text(editor.page, 'out-of-turn');
    return [t.includes(`Move 7 was made by ${ownerName}, not You`), t];
  }, LIVE_TIMEOUT);
  check((await text(editor.page, 'status')) === `Waiting for ${ownerName}`, 'and the turn has passed to the owner');

  // --- resign -----------------------------------------------------------------------------
  await el(page, 'resign').click();
  await el(page, 'resign-confirm').click();
  await until('resigning ends it; the editor wins', async () => {
    const g = (await games())[1];
    return [g.status === 'resigned' && g.winner === user(EDITOR).id && (await text(editor.page, 'status')) === `${ownerName} resigned: you win`, g];
  });

  // --- the viewer watches -----------------------------------------------------------------
  await openApp(viewer.page, instance, 'finished');
  check(!(await shown(viewer.page, 'new-game')), 'the viewer has no New game');
  await el(viewer.page, `game-${g1.id}`).click();
  await el(viewer.page, 'board').waitFor({ timeout: UI_TIMEOUT });
  check((await text(viewer.page, 'status')) === `${ownerName} won` && !(await shown(viewer.page, 'rematch')), 'the viewer sees the result, with no Rematch');
  const refused = await rpc(viewer, { op: 'action', name: 'dropDisc', params: { game_id: g1.id, turn: 7, col: 5, result: 'playing', winner: null } });
  check(refused.status === 403, 'a move as the viewer is refused', refused.status);

  await viewer.context.close();
  await editor.context.close();
  await owner.context.close();
  console.log('PASS: connect four smoke');
} finally {
  await browser.close();
}
