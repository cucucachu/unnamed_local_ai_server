// #477: the Calendar template in the browser. Driven by calendar_template_smoke.sh
// (which makes the users and cleans up); see its header for what is checked.
//
// Env: CAL_SMOKE_USER / _EDITOR / _VIEWER and their _PASSWORDs (e2e-*
// users), CAL_SMOKE_SPACE_SLUG (e2e-cal-*), CAL_SMOKE_STATE (a file this
// writes the app ids to, for the cleanup trap), CAL_SMOKE_BASE_URL (default
// http://localhost/), CAL_SMOKE_SCREENSHOTS (optional: a folder for the
// month, week and day views).
import fs from 'node:fs';
import { chromium } from 'playwright';

import { loginThroughUi } from './auth_helpers.mjs';

const BASE = (process.env.CAL_SMOKE_BASE_URL ?? 'http://localhost/').replace(/\/$/, '');
const OWNER = { username: process.env.CAL_SMOKE_USER, password: process.env.CAL_SMOKE_PASSWORD };
const EDITOR = { username: process.env.CAL_SMOKE_EDITOR, password: process.env.CAL_SMOKE_EDITOR_PASSWORD };
const VIEWER = { username: process.env.CAL_SMOKE_VIEWER, password: process.env.CAL_SMOKE_VIEWER_PASSWORD };
const SLUG = process.env.CAL_SMOKE_SPACE_SLUG;
const STATE = process.env.CAL_SMOKE_STATE;
if (![OWNER, EDITOR, VIEWER].every((u) => /^e2e-/.test(u.username ?? '') && u.password) || !/^e2e-cal-/.test(SLUG ?? '') || !STATE) {
  throw new Error('CAL_SMOKE_USER / _EDITOR / _VIEWER (e2e-*), their passwords, CAL_SMOKE_SPACE_SLUG (e2e-cal-*) and CAL_SMOKE_STATE are required');
}
const UI_TIMEOUT = 30_000;
const LIVE_TIMEOUT = 10_000;
const BUILD_TIMEOUT = 240_000;
const APP_FRAME = 'iframe[title="app"]';
const SHOTS = process.env.CAL_SMOKE_SCREENSHOTS;

const ok = (what) => console.log(`ok   ${what}`);
function check(cond, what, detail) {
  if (!cond) throw new Error(`FAIL: ${what}${detail === undefined ? '' : `: ${JSON.stringify(detail)}`}`);
  ok(what);
}

/** Polls `probe` (which returns [done, detail]) until done; returns how long it took. */
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
  const context = await browser.newContext({ viewport: { width: 1100, height: 900 } });
  const page = await context.newPage();
  await page.goto(`${BASE}/`);
  await loginThroughUi(page, user);
  const call = api(context);
  return { context, page, call, json: async (...args) => (await call(...args)).body };
}

const ui = (page) => page.frameLocator(APP_FRAME);
const el = (page, testID) => ui(page).getByTestId(testID);
const count = (page, testID) => el(page, testID).count();
const shown = async (page, testID) => (await count(page, testID)) > 0;

async function openApp(page, instance, readyTestID) {
  await page.goto(`${BASE}/apps/${instance.id}`);
  await page.getByTestId('app-runner').waitFor({ timeout: UI_TIMEOUT });
  await el(page, readyTestID).waitFor({ timeout: UI_TIMEOUT });
}

// Local dates, as the app (in a browser on this machine) sees them.
const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const shift = (day, n) => {
  const [y, m, d] = day.split('-').map(Number);
  return ymd(new Date(y, m - 1, d + n));
};
const TODAY = ymd(new Date());
const SUNDAY = shift(TODAY, -new Date().getDay());
const MON = shift(SUNDAY, 1);
const WED = shift(SUNDAY, 3);
const NEXT_MON = shift(MON, 7);
const NEXT_TUE = shift(MON, 8);
const NEXT_WED = shift(WED, 7);
const BIRTHDAY_DAY = `${TODAY.slice(0, 8)}15`;

const block = (id, start, day = start) => `block-e${id}-${start}-${day}`;

async function save(page) {
  await el(page, 'event-save').click();
  await el(page, 'event-title').waitFor({ state: 'detached', timeout: UI_TIMEOUT });
}

const browser = await chromium.launch();
try {
  const owner = await signIn(browser, OWNER);
  ok(`signed in as ${OWNER.username}`);

  // --- setup over REST: shared space, an editor and a viewer, Contacts and Calendar
  const shared = await owner.json('POST', '/api/platform/spaces', { json: { slug: SLUG, name: `E2E Calendar ${process.pid}` } });
  const directory = (await owner.json('GET', '/api/platform/users/directory')).users;
  const user = (u) => directory.find((d) => d.username === u.username);
  await owner.json('POST', `/api/platform/spaces/${shared.id}/members`, { json: { user_id: user(EDITOR).id, role: 'editor' } });
  await owner.json('POST', `/api/platform/spaces/${shared.id}/members`, { json: { user_id: user(VIEWER).id, role: 'viewer' } });
  const templates = (await owner.json('GET', '/api/platform/app-templates')).templates;
  check(templates.some((t) => t.slug === 'calendar'), 'the catalog lists Calendar');
  const installed = {};
  for (const slug of ['contacts', 'calendar']) {
    const out = await owner.json('POST', `/api/platform/spaces/${shared.id}/app-templates/${slug}/install`, { json: {}, timeout: BUILD_TIMEOUT });
    fs.appendFileSync(STATE, `${out.app.id}\n`);
    check(out.build?.ok && out.build.diagnostics.length === 0, `installed and built ${slug} with zero diagnostics`, out.build);
    installed[slug] = out.instance;
  }
  const rpc = (who, slug, body) => who.call('POST', `/api/platform/apps/instances/${installed[slug].id}/rpc`, { json: body, allow: [403] });
  const rows = async (sql, params = []) => (await rpc(owner, 'calendar', { op: 'getAll', sql, params })).body.rows;
  const events = () => rows('SELECT id, title, location, all_day, start_date, start_time, end_date, end_time, repeat, repeat_days FROM events ORDER BY id');
  const byTitle = async (title) => (await events()).filter((e) => e.title === title);

  const grandma = (await rpc(owner, 'contacts', { op: 'run', sql: 'INSERT INTO people (name, birthday) VALUES (?, ?)', params: ['Grandma', `1950-${BIRTHDAY_DAY.slice(5)}`] })).body.lastInsertRowId;
  const birthdayItem = `month-item-b${installed.contacts.id}-${grandma}-${TODAY.slice(0, 4)}`;

  const editor = await signIn(browser, EDITOR);
  const viewer = await signIn(browser, VIEWER);
  ok('signed in as the editor and the viewer');
  const { page } = owner;
  await openApp(page, installed.calendar, 'month-grid');
  await openApp(editor.page, installed.calendar, 'month-grid');
  ok('Calendar opens on the month for both');

  // --- month view and Contacts birthdays ---------------------------------------
  await until("Grandma's birthday from Contacts is on the 15th", async () => {
    const t = await el(page, birthdayItem).innerText({ timeout: 2000 });
    const inCell = await el(page, `month-day-${BIRTHDAY_DAY}`).getByTestId(birthdayItem).count();
    return [t === "Grandma's birthday" && inCell === 1, t];
  });

  // --- a timed event, from an empty hour in Day view ---------------------------
  await el(page, 'view-day').click();
  await el(page, `slot-${TODAY}-14`).click();
  await el(page, 'event-title').fill('Dentist');
  await el(page, 'event-location').fill('Main St clinic');
  await el(page, `who-${EDITOR.username}`).click();
  await save(page);
  const [dentist] = await byTitle('Dentist');
  check(
    dentist && dentist.start_date === TODAY && dentist.start_time === '14:00' && dentist.end_time === '15:00' && dentist.all_day === 0 && dentist.location === 'Main St clinic',
    'tapping 2 PM makes a 2–3 PM event that day',
    dentist,
  );
  const who = await rows('SELECT user_id FROM event_people WHERE event_id = ?', [dentist.id]);
  check(who.length === 1 && who[0].user_id === user(EDITOR).id, 'it is for the editor', who);
  await el(page, block(dentist.id, TODAY)).waitFor({ timeout: UI_TIMEOUT });
  ok('it shows in the Day view grid');
  const live = await until('the editor sees it in their month without reloading', async () => [await shown(editor.page, `month-item-e${dentist.id}-${TODAY}`), null], LIVE_TIMEOUT);
  console.log(`     cross-member refresh: ${live} ms`);

  // --- an all-day event over three days ----------------------------------------
  await el(page, 'new-event').click();
  await el(page, 'event-title').fill('Camping');
  await el(page, 'event-all-day').click();
  await el(page, 'event-end-date').fill(shift(TODAY, 2));
  await save(page);
  const [camping] = await byTitle('Camping');
  check(camping && camping.all_day === 1 && camping.start_date === TODAY && camping.end_date === shift(TODAY, 2) && camping.start_time === null, 'an all-day event over three days', camping);
  for (let i = 0; i < 4; i++) {
    const day = shift(TODAY, i);
    await el(page, `column-${day}`).waitFor({ timeout: UI_TIMEOUT });
    if (i < 3) await el(page, `allday-e${camping.id}-${TODAY}-${day}`).waitFor({ timeout: UI_TIMEOUT });
    else await el(page, `block-e${dentist.id}-${TODAY}-${TODAY}`).waitFor({ state: 'detached', timeout: UI_TIMEOUT });
    check((await shown(page, `allday-e${camping.id}-${TODAY}-${day}`)) === i < 3, `${day}: ${i < 3 ? 'in' : 'not in'} the all-day strip`);
    await el(page, 'next').click();
  }
  await el(page, 'today').click();

  // --- a weekly event on Mondays and Wednesdays --------------------------------
  await el(page, 'new-event').click();
  await el(page, 'event-title').fill('Soccer');
  await el(page, 'event-start-date').fill(MON);
  await el(page, 'event-start-time').fill('18:00');
  check((await el(page, 'event-end-time').inputValue()) === '19:00' && (await el(page, 'event-end-date').inputValue()) === MON, 'moving the start moves the end with it');
  await el(page, 'event-repeat').selectOption({ label: 'Weekly' });
  await el(page, 'repeat-day-3').click();
  await save(page);
  const [soccer] = await byTitle('Soccer');
  check(soccer && soccer.repeat === 'weekly' && soccer.repeat_days === '1,3' && soccer.start_date === MON, 'a weekly event on Mondays and Wednesdays', soccer);
  await el(page, 'view-week').click();
  await el(page, `column-${MON}`).waitFor({ timeout: UI_TIMEOUT });
  check((await shown(page, block(soccer.id, MON))) && (await shown(page, block(soccer.id, WED))) && !(await shown(page, block(soccer.id, shift(MON, 1)))), 'this week: on Monday and Wednesday only');
  check(await shown(page, block(dentist.id, TODAY)), 'the dentist is in the week too');
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/calendar-week.png` });
  await el(page, 'next').click();
  await el(page, `column-${NEXT_MON}`).waitFor({ timeout: UI_TIMEOUT });
  check((await shown(page, block(soccer.id, NEXT_MON))) && (await shown(page, block(soccer.id, NEXT_WED))), 'next week: Monday and Wednesday again');

  // --- edit one occurrence -----------------------------------------------------
  await el(page, block(soccer.id, NEXT_WED)).click();
  await el(page, 'detail-title').waitFor({ timeout: UI_TIMEOUT });
  check((await el(page, 'detail-repeat').innerText()) === 'Every week on Mon, Wed', 'the event screen says how it repeats');
  await el(page, 'edit-one').click();
  await el(page, 'one-occurrence').waitFor({ timeout: UI_TIMEOUT });
  check((await el(page, 'event-start-date').inputValue()) === NEXT_WED, 'editing one starts from that occurrence');
  await el(page, 'event-start-time').fill('17:00');
  await save(page);
  await el(page, `column-${NEXT_MON}`).waitFor({ timeout: UI_TIMEOUT });
  const moved = (await byTitle('Soccer')).find((e) => e.id !== soccer.id);
  const skips = await rows('SELECT event_id, date FROM event_skips ORDER BY id');
  check(moved && moved.start_date === NEXT_WED && moved.start_time === '17:00' && moved.end_time === '18:00' && moved.repeat === 'none', 'that one is now its own event at 5 PM', moved);
  check(skips.length === 1 && skips[0].event_id === soccer.id && skips[0].date === NEXT_WED, 'and the series skips it', skips);
  await until('the week shows the moved one instead', async () => [(await shown(page, block(moved.id, NEXT_WED))) && !(await shown(page, block(soccer.id, NEXT_WED))), null]);
  check(await shown(page, block(soccer.id, NEXT_MON)), 'the rest of the series stays');

  // --- delete one occurrence ---------------------------------------------------
  await el(page, block(soccer.id, NEXT_MON)).click();
  await el(page, 'delete').click();
  await el(page, 'delete-one').click();
  await el(page, `column-${NEXT_MON}`).waitFor({ timeout: UI_TIMEOUT });
  await until('deleting just that Monday leaves the series', async () => {
    const s = await rows('SELECT date FROM event_skips WHERE event_id = ? ORDER BY date', [soccer.id]);
    return [JSON.stringify(s.map((r) => r.date)) === JSON.stringify([NEXT_MON, NEXT_WED]) && !(await shown(page, block(soccer.id, NEXT_MON))), s];
  });
  check((await byTitle('Soccer')).length === 2, 'both Soccer rows are still there');
  check(!(await shown(page, block(soccer.id, NEXT_TUE))), 'and nothing appeared on Tuesday');

  // --- overlapping events sit side by side -------------------------------------
  await el(page, 'today').click();
  await el(page, 'view-day').click();
  const call = (await rpc(owner, 'calendar', { op: 'run', sql: "INSERT INTO events (title, start_date, start_time, end_date, end_time) VALUES ('Call', ?, '14:30', ?, '15:30')", params: [TODAY, TODAY] })).body.lastInsertRowId;
  await el(page, block(call, TODAY)).waitFor({ timeout: UI_TIMEOUT });
  const [a, b] = [await el(page, block(dentist.id, TODAY)).boundingBox(), await el(page, block(call, TODAY)).boundingBox()];
  check(Math.abs(a.width - b.width) < 2 && b.x >= a.x + a.width - 2 && b.y > a.y, 'two overlapping events share the column', { a, b });
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/calendar-day.png` });

  // --- one person's events ------------------------------------------------------
  await el(page, 'view-week').click();
  await el(page, 'person-filter').selectOption({ label: user(EDITOR).display_name });
  await until("filtering to the editor shows only the editor's dentist", async () => {
    const seen = { dentist: await shown(page, block(dentist.id, TODAY)), soccer: await shown(page, block(soccer.id, MON)), call: await shown(page, block(call, TODAY)) };
    return [seen.dentist && !seen.soccer && !seen.call, seen];
  });
  await el(page, 'view-month').click();
  check(!(await shown(page, birthdayItem)) && (await shown(page, `month-item-e${dentist.id}-${TODAY}`)), 'and no birthdays');
  await el(page, 'person-filter').selectOption({ label: 'Everyone' });
  await el(page, birthdayItem).waitFor({ timeout: UI_TIMEOUT });
  ok('Everyone brings it all back');
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/calendar-month.png` });

  // --- the viewer looks only ---------------------------------------------------
  await openApp(viewer.page, installed.calendar, 'month-grid');
  check(!(await shown(viewer.page, 'new-event')), 'the viewer has no +');
  await el(viewer.page, `month-day-${TODAY}`).click();
  await el(viewer.page, `slot-${TODAY}-10`).click({ force: true });
  await new Promise((r) => setTimeout(r, 300));
  check(!(await shown(viewer.page, 'event-title')), 'tapping an empty hour does nothing for the viewer');
  await el(viewer.page, block(dentist.id, TODAY)).click();
  await el(viewer.page, 'detail-title').waitFor({ timeout: UI_TIMEOUT });
  check((await el(viewer.page, 'detail-location').innerText()) === 'Main St clinic' && (await el(viewer.page, 'detail-who').innerText()) === user(EDITOR).display_name, 'the viewer sees where and who');
  check(!(await shown(viewer.page, 'edit-all')) && !(await shown(viewer.page, 'delete')), 'but no Edit or Delete');
  const refused = await rpc(viewer, 'calendar', { op: 'run', sql: "INSERT INTO events (title, all_day, start_date, end_date) VALUES ('sneaky', 1, ?, ?)", params: [TODAY, TODAY] });
  check(refused.status === 403 && (await byTitle('sneaky')).length === 0, 'a write as the viewer is refused', refused.status);

  // --- delete a whole series ---------------------------------------------------
  await el(page, 'view-week').click();
  await el(page, block(soccer.id, MON)).click();
  await el(page, 'delete').click();
  await el(page, 'delete-all').click();
  await until('Delete all events removes the series and its skips', async () => {
    const [s, k] = [await byTitle('Soccer'), await rows('SELECT count(*) AS n FROM event_skips')];
    return [s.length === 1 && s[0].id === moved.id && k[0].n === 0, { s, k }];
  });

  await viewer.context.close();
  await editor.context.close();
  await owner.context.close();
  console.log('PASS: calendar template smoke');
} finally {
  await browser.close();
}
