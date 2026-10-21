// #480: the Daily Word template in the browser. Driven by daily_word_smoke.sh
// (which makes the users and cleans up); see its header for what is checked.
//
// Env: WORD_SMOKE_USER / _EDITOR / _VIEWER and their _PASSWORDs (e2e-*
// users), WORD_SMOKE_SPACE_SLUG (e2e-word-*), WORD_SMOKE_STATE (a file this
// writes the app id to, for the cleanup trap), WORD_SMOKE_BASE_URL (default
// http://localhost/), WORD_SMOKE_SCREENSHOTS (optional: a folder for the
// boards and the Today tab).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

import { loginThroughUi } from './auth_helpers.mjs';

const BASE = (process.env.WORD_SMOKE_BASE_URL ?? 'http://localhost/').replace(/\/$/, '');
const OWNER = { username: process.env.WORD_SMOKE_USER, password: process.env.WORD_SMOKE_PASSWORD };
const EDITOR = { username: process.env.WORD_SMOKE_EDITOR, password: process.env.WORD_SMOKE_EDITOR_PASSWORD };
const VIEWER = { username: process.env.WORD_SMOKE_VIEWER, password: process.env.WORD_SMOKE_VIEWER_PASSWORD };
const SLUG = process.env.WORD_SMOKE_SPACE_SLUG;
const STATE = process.env.WORD_SMOKE_STATE;
if (![OWNER, EDITOR, VIEWER].every((u) => /^e2e-/.test(u.username ?? '') && u.password) || !/^e2e-word-/.test(SLUG ?? '') || !STATE) {
  throw new Error('WORD_SMOKE_USER / _EDITOR / _VIEWER (e2e-*), their passwords, WORD_SMOKE_SPACE_SLUG (e2e-word-*) and WORD_SMOKE_STATE are required');
}
const UI_TIMEOUT = 30_000;
const LIVE_TIMEOUT = 10_000;
const BUILD_TIMEOUT = 240_000;
const APP_FRAME = 'iframe[title="app"]';
const SHOTS = process.env.WORD_SMOKE_SCREENSHOTS;

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

async function openApp(page, instance, readyTestID) {
  await page.goto(`${BASE}/apps/${instance.id}`);
  await page.getByTestId('app-runner').waitFor({ timeout: UI_TIMEOUT });
  await el(page, readyTestID).waitFor({ timeout: UI_TIMEOUT });
}

// --- the game's own rules, from the template ------------------------------------
const WORDS_TS = fs.readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../examples/apps/daily-word/lib/words.ts'), 'utf8');
const list = (name) => WORDS_TS.match(new RegExp(`${name}[^=]*= (?:new Set\\()?'([a-z ]+)'`))[1].split(' ');
const ANSWERS = list('ANSWERS');
const GUESSES = list('GUESSES');
const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const TODAY = ymd(new Date());
const dayNumber = (day) => Date.UTC(...day.split('-').map((n, i) => Number(n) - (i === 1 ? 1 : 0))) / 86_400_000;
const n = dayNumber(TODAY) - dayNumber('2026-01-01');
const WORD = ANSWERS[((n % ANSWERS.length) + ANSWERS.length) % ANSWERS.length];
function mark(guess, word) {
  const marks = Array(5).fill('absent');
  const left = {};
  for (let i = 0; i < 5; i++) {
    if (guess[i] === word[i]) marks[i] = 'correct';
    else left[word[i]] = (left[word[i]] ?? 0) + 1;
  }
  for (let i = 0; i < 5; i++) {
    if (marks[i] !== 'correct' && left[guess[i]]) {
      marks[i] = 'present';
      left[guess[i]]--;
    }
  }
  return marks;
}
// A guess that repeats a letter the word has once, elsewhere: one copy present, the other absent.
const REPEATED = GUESSES.find((g) => {
  if (g === WORD) return false;
  const m = mark(g, WORD);
  return [...new Set(g)].some((c) => g.split(c).length - 1 === 2 && WORD.split(c).length - 1 === 1 && [...g].every((x, i) => x !== c || m[i] !== 'correct') && m.filter((k, i) => g[i] === c && k === 'present').length === 1);
});
const WRONG = GUESSES.filter((g) => g !== WORD && g !== REPEATED).slice(0, 6);

async function typeGuess(page, guess) {
  for (const c of guess) await el(page, `key-${c}`).click();
  await el(page, 'key-enter').click();
}
async function tiles(page, row) {
  const out = [];
  for (let c = 0; c < 5; c++) out.push(await el(page, `tile-${row}-${c}`).getAttribute('aria-label'));
  return out;
}
const expected = (guess) => mark(guess, WORD).map((m, i) => `${guess[i]} ${m}`);

const browser = await chromium.launch();
try {
  console.log(`     today's word is "${WORD}"; the repeated-letter guess is "${REPEATED}"`);
  check(REPEATED, 'found a guess with a repeated letter for today’s word');
  const owner = await signIn(browser, OWNER);
  ok(`signed in as ${OWNER.username}`);

  // --- setup over REST ----------------------------------------------------------------
  const shared = await owner.json('POST', '/api/platform/spaces', { json: { slug: SLUG, name: `E2E Word ${process.pid}` } });
  const directory = (await owner.json('GET', '/api/platform/users/directory')).users;
  const user = (u) => directory.find((d) => d.username === u.username);
  await owner.json('POST', `/api/platform/spaces/${shared.id}/members`, { json: { user_id: user(EDITOR).id, role: 'editor' } });
  await owner.json('POST', `/api/platform/spaces/${shared.id}/members`, { json: { user_id: user(VIEWER).id, role: 'viewer' } });
  const out = await owner.json('POST', `/api/platform/spaces/${shared.id}/app-templates/daily-word/install`, { json: {}, timeout: BUILD_TIMEOUT });
  fs.appendFileSync(STATE, `${out.app.id}\n`);
  check(out.build?.ok && out.build.diagnostics.length === 0, 'installed and built Daily Word with zero diagnostics', out.build);
  const instance = out.instance;
  const rpc = (who, body) => who.call('POST', `/api/platform/apps/instances/${instance.id}/rpc`, { json: body, allow: [403] });
  const games = async () => (await rpc(owner, { op: 'getAll', sql: 'SELECT day, word, guesses, status, _created_by FROM games ORDER BY id', params: [] })).body.rows;

  const editor = await signIn(browser, EDITOR);
  const viewer = await signIn(browser, VIEWER);
  ok('signed in as the editor and the viewer');
  const { page } = owner;
  await openApp(page, instance, 'keyboard');
  ok('Daily Word opens on an empty board');

  // --- the owner plays -----------------------------------------------------------------
  await typeGuess(page, 'zzzzz');
  check((await text(page, 'message')) === 'Not in the word list', 'a non-word is refused');
  for (let i = 0; i < 5; i++) await el(page, 'key-back').click();
  await typeGuess(page, 'abc');
  check((await text(page, 'message')) === 'Not enough letters', 'a short guess is refused');
  for (let i = 0; i < 3; i++) await el(page, 'key-back').click();
  await el(page, 'hardware-keys').focus();
  await page.keyboard.type(REPEATED);
  await page.keyboard.press('Enter');
  await until('a guess typed on a hardware keyboard is saved', async () => {
    const g = await games();
    return [g.length === 1 && g[0].guesses === REPEATED && g[0].status === 'playing' && g[0].day === TODAY && g[0].word === WORD && g[0]._created_by === user(OWNER).id, g];
  });
  check(JSON.stringify(await tiles(page, 0)) === JSON.stringify(expected(REPEATED)), 'the repeated letter is marked present once and absent once', await tiles(page, 0));

  await openApp(page, instance, 'keyboard');
  await el(page, 'tile-0-0').waitFor({ timeout: UI_TIMEOUT });
  await until('reopening the app resumes the game', async () => [JSON.stringify(await tiles(page, 0)) === JSON.stringify(expected(REPEATED)), await tiles(page, 0)]);
  await typeGuess(page, WORD);
  await el(page, 'result').waitFor({ timeout: UI_TIMEOUT });
  check((await text(page, 'result-title')) === 'Got it in 2!', 'guessing the word wins in 2');
  check(!(await shown(page, 'keyboard')), 'and the keyboard goes away');
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/word-owner.png` });

  // --- the editor plays and loses -----------------------------------------------------
  await openApp(editor.page, instance, 'keyboard');
  await el(editor.page, 'tab-today').click();
  await el(editor.page, `player-${OWNER.username}`).waitFor({ timeout: UI_TIMEOUT });
  check((await text(editor.page, `player-${OWNER.username}`)).includes('Solved in 2'), 'the editor sees the owner solved it in 2');
  check(!(await shown(editor.page, `grid-${OWNER.username}`)), 'but not the owner’s grid before playing');
  await el(page, 'tab-today').click();
  await el(page, `player-${EDITOR.username}`).waitFor({ timeout: UI_TIMEOUT });
  await el(editor.page, 'tab-index').click();
  for (const g of WRONG) await typeGuess(editor.page, g);
  await el(editor.page, 'result').waitFor({ timeout: UI_TIMEOUT });
  check((await text(editor.page, 'result-title')) === `The word was ${WORD.toUpperCase()}`, 'six misses lose and show the word');
  const live = await until('the owner’s Today tab shows the editor’s result without reloading', async () => [(await text(page, `player-${EDITOR.username}`)).includes('Out of guesses'), null], LIVE_TIMEOUT);
  console.log(`     cross-member refresh: ${live} ms`);
  await el(editor.page, 'see-today').click();
  await el(editor.page, `grid-${OWNER.username}`).waitFor({ timeout: UI_TIMEOUT });
  check((await el(editor.page, `grid-${OWNER.username}`).locator('> div').count()) === 2, 'finished, the editor sees the owner’s two-row grid');
  if (SHOTS) await editor.page.screenshot({ path: `${SHOTS}/word-today.png` });
  const rows = await games();
  check(rows.length === 2 && rows[1]._created_by === user(EDITOR).id && rows[1].status === 'lost' && rows[1].guesses.split(',').length === 6, 'one game each, stamped with the player', rows);

  // --- stats --------------------------------------------------------------------------
  await el(page, 'tab-stats').click();
  await el(page, 'stat-played').waitFor({ timeout: UI_TIMEOUT });
  const stats = { played: await text(page, 'stat-played'), win: await text(page, 'stat-win'), streak: await text(page, 'stat-streak'), best: await text(page, 'stat-best'), two: await text(page, 'spread-2') };
  check(JSON.stringify(stats) === JSON.stringify({ played: '1', win: '100', streak: '1', best: '1', two: '1' }), 'the owner’s stats: 1 played, 100%, streak 1, a win in 2', stats);
  check((await text(page, `stats-${EDITOR.username}`)).includes('1 played · 0% won · streak 0'), 'and the editor’s in the list');

  // --- the viewer -----------------------------------------------------------------------
  await openApp(viewer.page, instance, 'view-only');
  check(!(await shown(viewer.page, 'keyboard')), 'the viewer has no keyboard');
  await el(viewer.page, 'tab-today').click();
  await el(viewer.page, `grid-${EDITOR.username}`).waitFor({ timeout: UI_TIMEOUT });
  check(!(await shown(viewer.page, `player-${VIEWER.username}`)), 'the viewer sees both grids and isn’t listed as a player');
  const refused = await rpc(viewer, { op: 'run', sql: "INSERT INTO games (day, word) VALUES ('2026-01-01', 'crane')", params: [] });
  check(refused.status === 403 && (await games()).length === 2, 'a write as the viewer is refused', refused.status);

  await viewer.context.close();
  await editor.context.close();
  await owner.context.close();
  console.log('PASS: daily word smoke');
} finally {
  await browser.close();
}
