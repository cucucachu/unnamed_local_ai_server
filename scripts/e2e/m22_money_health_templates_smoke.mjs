// M22-17: the money and health templates in the browser. Driven by
// m22_money_health_templates_smoke.sh (which makes the user and cleans up);
// see its header for what is checked.
//
// Env: MONEY_SMOKE_USER / MONEY_SMOKE_PASSWORD (an e2e-* user),
// MONEY_SMOKE_STATE (a file this writes the app ids to, for the cleanup
// trap), MONEY_SMOKE_BASE_URL (default http://localhost/).
import fs from 'node:fs';
import { chromium } from 'playwright';

import { loginThroughUi } from './auth_helpers.mjs';

const BASE = (process.env.MONEY_SMOKE_BASE_URL ?? 'http://localhost/').replace(/\/$/, '');
const USER = { username: process.env.MONEY_SMOKE_USER, password: process.env.MONEY_SMOKE_PASSWORD };
const STATE = process.env.MONEY_SMOKE_STATE;
if (!/^e2e-/.test(USER.username ?? '') || !USER.password || !STATE) {
  throw new Error('MONEY_SMOKE_USER (e2e-*), MONEY_SMOKE_PASSWORD and MONEY_SMOKE_STATE are required');
}
const UI_TIMEOUT = 30_000;
const BUILD_TIMEOUT = 240_000;
const LONG_PRESS = { delay: 900 };
const APP_FRAME = 'iframe[title="app"]';

const ok = (what) => console.log(`ok   ${what}`);
function check(cond, what, detail) {
  if (!cond) throw new Error(`FAIL: ${what}${detail === undefined ? '' : `: ${JSON.stringify(detail)}`}`);
  ok(what);
}

/** Polls `probe` (which returns [done, detail]) until done. */
async function until(what, probe, timeout = UI_TIMEOUT) {
  const deadline = Date.now() + timeout;
  let detail;
  while (Date.now() < deadline) {
    let done;
    try {
      [done, detail] = await probe();
    } catch (e) {
      [done, detail] = [false, e.message];
    }
    if (done) return ok(what);
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`FAIL: ${what}: ${JSON.stringify(detail)}`);
}

function localDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function inDays(n) {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return localDate(d);
}
const TODAY = localDate(new Date());
const MONTH = TODAY.slice(0, 7);
const money = (n) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n);

const browser = await chromium.launch();
try {
  const context = await browser.newContext({ locale: 'en-US' });
  const page = await context.newPage();
  const call = async (method, url, { json, raw, timeout = 30_000 } = {}) => {
    const r = await context.request.fetch(`${BASE}${url}`, {
      method,
      timeout,
      ...(raw !== undefined ? { data: raw, headers: { 'Content-Type': 'application/octet-stream' } } : json !== undefined ? { data: json } : {}),
    });
    const text = await r.text();
    if (!r.ok()) throw new Error(`${method} ${url}: ${r.status()} ${text}`);
    return text ? JSON.parse(text) : null;
  };
  await page.goto(`${BASE}/`);
  await loginThroughUi(page, USER);
  ok(`signed in as ${USER.username}`);

  const personal = (await call('GET', '/api/platform/spaces')).spaces.find((s) => s.kind === 'personal');
  const installed = {};
  const install = async (slug) => {
    const out = await call('POST', `/api/platform/spaces/${personal.id}/app-templates/${slug}/install`, { json: {}, timeout: BUILD_TIMEOUT });
    fs.appendFileSync(STATE, `${out.app.id}\n`);
    check(out.build?.ok && out.build.diagnostics.length === 0, `installed and built ${slug} with zero diagnostics`, out.build);
    installed[slug] = out.instance;
  };
  for (const slug of ['budget', 'workouts', 'health-log', 'countdowns']) await install(slug);
  const rpc = (slug, body) => call('POST', `/api/platform/apps/instances/${installed[slug].id}/rpc`, { json: body });
  const rows = async (slug, sql, params = []) => (await rpc(slug, { op: 'getAll', sql, params })).rows;

  const ui = page.frameLocator(APP_FRAME);
  const text = (testID) => ui.getByTestId(testID).innerText({ timeout: 2000 });
  const label = (testID) => ui.getByTestId(testID).getAttribute('aria-label', { timeout: 2000 });
  const openApp = async (slug, readyTestID) => {
    await page.goto(`${BASE}/apps/${installed[slug].id}`);
    await page.getByTestId('app-runner').waitFor({ timeout: UI_TIMEOUT });
    await ui.getByTestId(readyTestID).waitFor({ timeout: UI_TIMEOUT });
  };
  const modalClosed = () => ui.getByTestId('modal').waitFor({ state: 'detached', timeout: UI_TIMEOUT });

  // --- Budget ----------------------------------------------------------------
  await openApp('budget', 'month-empty');
  ok('Budget opens on an empty month');
  await ui.getByTestId('tab-categories').click();
  await ui.getByRole('button', { name: 'Add the usual ones' }).click();
  await until('"Add the usual ones" adds seven categories', async () => [(await rows('budget', 'SELECT id FROM categories')).length === 7, null]);
  const cat = Object.fromEntries((await rows('budget', 'SELECT id, name FROM categories')).map((c) => [c.name, c.id]));
  await ui.getByTestId(`cat-${cat.Groceries}`).click();
  await ui.getByTestId('cat-edit-budget').fill('400');
  await ui.getByTestId('cat-save').click();
  await until('Groceries gets a $400 monthly budget', async () => {
    const [g] = await rows('budget', 'SELECT monthly_budget FROM categories WHERE id = ?', [cat.Groceries]);
    return [g.monthly_budget === 400, g];
  });

  const statement = [
    'Date,Description,Amount,Balance',
    `${MONTH}-01,TRADER JOE'S #552,-82.40,1917.60`,
    `${MONTH}-02,SHELL OIL 1234,-45.00,1872.60`,
    `${MONTH}-03,Trader Joe's #552,-36.15,1836.45`,
    `${MONTH}-04,ACME CORP PAYROLL,3000.00,4836.45`,
    `${MONTH}-05,BLUE BOTTLE COFFEE,-6.50,4829.95`,
  ].join('\n');
  const bad = ['Date,Description,Amount', `${MONTH}-01,OK,-1.00`, '10/03/2026,US date,-2.00'].join('\n');
  const put = (path, content) => call('PUT', `/api/platform/files/content?path=${encodeURIComponent(path)}`, { raw: Buffer.from(content) });
  await put('/personal/statement.csv', `${statement}\n`);
  await put('/personal/bad-statement.csv', `${bad}\n`);

  await page.getByTestId('app-info-button').click();
  await page.getByTestId('app-import').waitFor({ timeout: UI_TIMEOUT });
  await page.getByTestId('app-import-table-transactions').click();
  await page.getByTestId('app-import-path').fill('/personal/bad-statement.csv');
  await page.getByTestId('app-import-check').click();
  await page.getByTestId('app-import-map-Date-day').click();
  await page.getByTestId('app-import-confirm').click();
  await until('a statement with a US-style date is refused at its line', async () => {
    const msg = await page.getByTestId('app-info-error').innerText({ timeout: 2000 });
    return [msg.startsWith('Nothing was imported.') && msg.includes('Line 3'), msg];
  });
  await page.getByTestId('app-import-path').fill('/personal/statement.csv');
  await page.getByTestId('app-import-check').click();
  await until('Check file asks for a column for Date and Balance', async () => {
    const msg = await page.getByTestId('app-import-summary').innerText({ timeout: 2000 });
    return [msg.includes('Date') && msg.includes('Balance'), msg];
  });
  await page.getByTestId('app-import-map-Date-day').click();
  await page.getByTestId('app-import-skip-Balance').click();
  await page.getByTestId('app-import-confirm').click();
  await until('mapping Date to day and skipping Balance imports five rows', async () => {
    const msg = await page.getByTestId('app-info-notice').innerText({ timeout: 2000 });
    return [msg === 'Imported 5 rows into transactions.', msg];
  });
  const txns = await rows('budget', 'SELECT id, day, description, amount, category_id FROM transactions ORDER BY id');
  check(txns.length === 5 && txns[0].amount === -82.4 && txns[3].amount === 3000 && txns.every((t) => t.category_id === null), 'the rows are in, signed, uncategorized', txns);
  const txn = (desc) => txns.find((t) => t.description === desc).id;

  await openApp('budget', 'month-label');
  await ui.getByTestId('tab-transactions').click();
  await ui.getByTestId(`txn-${txn("TRADER JOE'S #552")}`).click();
  await ui.getByTestId('txn-category').selectOption({ label: 'Groceries' });
  await ui.getByTestId('txn-remember').click();
  const suggested = await ui.getByTestId('txn-pattern').inputValue();
  check(suggested === "TRADER JOE'S", 'the rule suggests the description without its store number', suggested);
  await ui.getByTestId('txn-save').click();
  await until('"Always use this category" files both Trader Joe\'s rows under Groceries', async () => {
    const r = await rows('budget', 'SELECT id, category_id FROM transactions WHERE description LIKE ?', ['%joe%']);
    const rules = await rows('budget', 'SELECT pattern, category_id FROM rules');
    return [r.length === 2 && r.every((x) => x.category_id === cat.Groceries) && rules.length === 1 && rules[0].pattern === "TRADER JOE'S", { r, rules }];
  });
  await ui.getByTestId(`txn-${txn('ACME CORP PAYROLL')}`).click();
  await ui.getByTestId('txn-category').selectOption({ label: 'Salary' });
  await ui.getByTestId('txn-save').click();
  await until('categorizing the salary keeps it positive', async () => {
    const [p] = await rows('budget', 'SELECT amount, category_id FROM transactions WHERE id = ?', [txn('ACME CORP PAYROLL')]);
    return [p.amount === 3000 && p.category_id === cat.Salary, p];
  });

  const addTxn = async (description, amount, category) => {
    await ui.getByTestId('new-transaction').click();
    await ui.getByTestId('modal').waitFor({ timeout: UI_TIMEOUT });
    await ui.getByTestId('txn-description').fill(description);
    await ui.getByTestId('txn-amount').fill(String(amount));
    if (category) await ui.getByTestId('txn-category').selectOption({ label: category });
    await ui.getByTestId('txn-save').click();
    await modalClosed();
  };
  await addTxn('Farmers market', 20, 'Groceries');
  await addTxn("Trader Joe's #9", 12.35);
  await until('the Add modal saves spending as negative', async () => {
    const r = await rows('budget', "SELECT amount, category_id FROM transactions WHERE description = 'Farmers market'");
    return [r[0]?.amount === -20 && r[0]?.category_id === cat.Groceries, r];
  });
  await ui.getByTestId('apply-rules').click();
  await until('"Apply my rules" categorizes the new Trader Joe\'s row', async () => {
    const [r] = await rows('budget', "SELECT category_id FROM transactions WHERE description = 'Trader Joe''s #9'");
    return [r?.category_id === cat.Groceries && (await text('rules-message')) === 'Categorized 1 transaction.', r];
  });

  await ui.getByTestId('tab-index').click();
  const spent = 82.4 + 45 + 36.15 + 6.5 + 20 + 12.35;
  const groceries = 82.4 + 36.15 + 20 + 12.35;
  await until('the month totals what was spent and what came in', async () => {
    const s = await text('month-spent');
    const i = await text('month-income');
    return [s === money(spent) && i === money(3000), { s, i, want: money(spent) }];
  });
  await until('Groceries shows its spending against the budget', async () => [(await text(`budget-${cat.Groceries}`)).includes(`${money(groceries)} of ${money(400)}`), await text(`budget-${cat.Groceries}`)]);
  await until('the pie chart has Groceries and Uncategorized', async () => {
    const l = await label('spend-pie');
    return [l?.includes('Groceries') && l?.includes('Uncategorized'), l];
  });
  await ui.getByTestId('tab-trends').click();
  await until('the trend chart has this month', async () => {
    const l = await label('trend-chart');
    return [l?.includes(money(spent)) && l?.includes(money(3000)), l];
  });

  // --- Workouts --------------------------------------------------------------
  await openApp('workouts', 'workouts-empty');
  ok('Workouts opens empty');
  await ui.getByTestId('tab-exercises').click();
  await ui.getByTestId('exercise-name').fill('Squat');
  await ui.getByTestId('exercise-add').click();
  await until('an exercise is added', async () => [(await rows('workouts', 'SELECT id FROM exercises')).length === 1, null]);
  const [squat] = await rows('workouts', 'SELECT id FROM exercises');
  const old = await rpc('workouts', { op: 'run', sql: 'INSERT INTO workouts (day) VALUES (?)', params: [inDays(-7)] });
  for (const w of [55, 55]) {
    await rpc('workouts', { op: 'action', name: 'addSet', params: { workout_id: old.lastInsertRowId, exercise_id: squat.id, reps: 5, weight: w } });
  }

  await ui.getByTestId('tab-index').click();
  await ui.getByTestId(`workout-${old.lastInsertRowId}`).waitFor({ timeout: UI_TIMEOUT });
  await ui.getByTestId('new-workout').click();
  await ui.getByTestId('workout-no-sets').waitFor({ timeout: UI_TIMEOUT });
  const [today] = await rows('workouts', 'SELECT id, day FROM workouts ORDER BY id DESC LIMIT 1');
  check(today.day === TODAY && today.id !== old.lastInsertRowId, '"+" starts a workout today');
  await ui.getByTestId('set-exercise').selectOption({ label: 'Squat' });
  for (const w of ['60', '62.5']) {
    await ui.getByTestId('set-weight').fill(w);
    await ui.getByTestId('set-add').click();
    await until(`a ${w} set is added`, async () => [(await rows('workouts', 'SELECT id FROM sets WHERE workout_id = ? AND weight = ?', [today.id, Number(w)])).length === 1, null]);
  }
  const [wrong] = await rows('workouts', 'SELECT id FROM sets WHERE workout_id = ? AND weight = 62.5', [today.id]);
  await ui.getByTestId(`set-${wrong.id}`).click(LONG_PRESS);
  await ui.getByTestId(`set-${wrong.id}-action-0`).click();
  await until('long-press, Delete removes a set', async () => [(await rows('workouts', 'SELECT id FROM sets WHERE id = ?', [wrong.id])).length === 0, null]);
  await ui.getByTestId('set-weight').fill('65');
  await ui.getByTestId('set-add').click();
  await until('the sets are in order (addSet)', async () => {
    const s = await rows('workouts', 'SELECT reps, weight, position FROM sets WHERE workout_id = ? ORDER BY position', [today.id]);
    return [JSON.stringify(s.map((x) => [x.reps, x.weight])) === '[[5,60],[5,65]]', s];
  });

  await ui.getByTestId('workout-repeat').click();
  await until('"Repeat this workout today" copies its sets into a new workout', async () => {
    const [w] = await rows('workouts', 'SELECT id, day FROM workouts ORDER BY id DESC LIMIT 1');
    const s = w ? await rows('workouts', 'SELECT weight FROM sets WHERE workout_id = ? ORDER BY position', [w.id]) : [];
    return [w.id !== today.id && w.day === TODAY && JSON.stringify(s.map((x) => x.weight)) === '[60,65]', { w, s }];
  });
  await ui.getByTestId('header-back').click();
  await ui.getByTestId('tab-exercises').click();
  await ui.getByTestId(`exercise-${squat.id}`).click();
  await until('the progress chart goes from 55 to 65', async () => {
    const l = await label('progress-chart');
    return [l?.includes('from 55') && l?.includes('highest 65'), l];
  });
  await ui.getByTestId('volume-chart').waitFor({ timeout: UI_TIMEOUT });
  await ui.getByTestId('exercise-edit-note').fill('Knees out');
  await ui.getByTestId('exercise-save').click();
  await until('the exercise note saves', async () => [(await rows('workouts', 'SELECT note FROM exercises'))[0].note === 'Knees out', null]);

  // --- Health log ------------------------------------------------------------
  await openApp('health-log', 'measures-empty');
  ok('Health log opens empty');
  await ui.getByRole('button', { name: 'Add weight, sleep and water' }).click();
  await until('weight, sleep and water are added', async () => [(await rows('health-log', 'SELECT id FROM measures')).length === 3, null]);
  const measure = Object.fromEntries((await rows('health-log', 'SELECT id, name FROM measures')).map((m) => [m.name, m.id]));
  const log = async (name, value, day) => {
    await ui.getByTestId('new-entry').click();
    await ui.getByTestId('modal').waitFor({ timeout: UI_TIMEOUT });
    await ui.getByTestId('log-measure').selectOption({ label: name });
    await ui.getByTestId('log-value').fill(String(value));
    if (day) await ui.getByTestId('log-day').fill(day);
    if (name === 'Water' && value === 2) {
      await until('logging more water says how much so far', async () => [(await text('log-sofar')).startsWith('3 glasses so far'), await text('log-sofar')]);
    }
    await ui.getByTestId('log-save').click();
    await modalClosed();
  };
  await log('Weight', 73.1, inDays(-7));
  await log('Weight', 72.4);
  await log('Water', 3);
  await log('Water', 2);
  await until("water adds up to today's 5 glasses", async () => [(await text(`measure-${measure.Water}`)).includes('5 glasses'), await text(`measure-${measure.Water}`)]);
  await until('weight shows the latest reading', async () => [(await text(`measure-${measure.Weight}`)).includes('72.4 kg'), await text(`measure-${measure.Weight}`)]);

  await ui.getByTestId(`measure-${measure.Weight}`).click();
  await until('the weight chart goes from 73.1 to 72.4', async () => {
    const l = await label('measure-chart');
    return [l?.includes('from 73.1 kg') && l?.includes('to 72.4 kg'), l];
  });
  check((await text('measure-stats')).includes('over 2 days'), 'the stats average the two days', await text('measure-stats'));
  await ui.getByTestId('m-goal').fill('70');
  await ui.getByTestId('m-save').click();
  await until('a goal saves and joins the chart', async () => {
    const [m] = await rows('health-log', 'SELECT goal FROM measures WHERE id = ?', [measure.Weight]);
    const l = await label('measure-chart');
    return [m.goal === 70 && l?.includes('Goal'), { m, l }];
  });
  const [first] = await rows('health-log', 'SELECT id FROM entries WHERE measure_id = ? ORDER BY day LIMIT 1', [measure.Weight]);
  await ui.getByTestId(`entry-${first.id}`).click(LONG_PRESS);
  await ui.getByTestId(`entry-${first.id}-action-0`).click();
  await until('long-press, Delete removes a reading', async () => [(await rows('health-log', 'SELECT id FROM entries WHERE id = ?', [first.id])).length === 0, null]);

  await ui.getByTestId('header-back').click();
  await ui.getByTestId('new-measure').click();
  await ui.getByTestId('modal').waitFor({ timeout: UI_TIMEOUT });
  await ui.getByTestId('m-name').fill('Steps');
  await ui.getByTestId('m-unit').fill('steps');
  await ui.getByTestId('m-summary').selectOption({ label: 'Add them up (water, steps)' });
  await ui.getByTestId('m-save').click();
  await until('a new measure opens on its own screen', async () => [(await text('measure-stats')) === 'No readings in this range.', await text('measure-stats')]);
  const [steps] = await rows('health-log', "SELECT summary FROM measures WHERE name = 'Steps'");
  check(steps?.summary === 'sum', 'it adds up a day', steps);

  // --- Countdowns, then Contacts' birthdays ---------------------------------
  await openApp('countdowns', 'countdowns-empty');
  await ui.getByTestId('no-contacts').waitFor({ timeout: UI_TIMEOUT });
  ok('Countdowns opens empty and says Contacts is missing (homeai_exports is empty)');
  const addCountdown = async (title, day, yearly = false) => {
    await ui.getByTestId('new-countdown').click();
    await ui.getByTestId('modal').waitFor({ timeout: UI_TIMEOUT });
    await ui.getByTestId('cd-title').fill(title);
    if (yearly) await ui.getByTestId('cd-yearly').click();
    await ui.getByTestId('cd-day').fill(day);
    await ui.getByTestId('cd-save').click();
    await modalClosed();
  };
  const soon = inDays(5);
  await addCountdown('Lisbon trip', inDays(30));
  await addCountdown('Wedding anniversary', `${Number(soon.slice(0, 4)) - 3}${soon.slice(4)}`, true);
  await addCountdown('Exam', inDays(-3));
  const cds = Object.fromEntries((await rows('countdowns', 'SELECT id, title FROM countdowns')).map((c) => [c.title, c.id]));
  await until('the trip is in 30 days', async () => [(await text(`countdown-${cds['Lisbon trip']}`)).includes('In 30 days'), await text(`countdown-${cds['Lisbon trip']}`)]);
  await until('the anniversary is in 5 days, the 3rd', async () => [(await text(`countdown-${cds['Wedding anniversary']}`)).includes('In 5 days · 3rd anniversary'), await text(`countdown-${cds['Wedding anniversary']}`)]);
  check((await ui.getByTestId('past').getByTestId(`countdown-${cds.Exam}`).innerText()).includes('3 days ago'), 'the exam is under Past, 3 days ago');
  check((await text('next-days')) === '5', 'the anniversary is next up', await text('next-days'));
  await ui.getByTestId(`countdown-${cds['Lisbon trip']}`).click();
  await ui.getByTestId('cd-title').fill('Lisbon and Porto');
  await ui.getByTestId('cd-save').click();
  await until('editing a countdown saves it', async () => [(await rows('countdowns', 'SELECT title FROM countdowns WHERE id = ?', [cds['Lisbon trip']]))[0].title === 'Lisbon and Porto', null]);

  await install('contacts');
  const bday = `1990-${inDays(2).slice(5)}`;
  const person = await rpc('contacts', { op: 'run', sql: 'INSERT INTO people (name, birthday) VALUES (?, ?)', params: ['Priya Shah', bday] });
  const birthday = `birthday-${installed.contacts.id}-${person.lastInsertRowId}`;
  await openApp('countdowns', 'upcoming');
  await until("a contact's birthday shows up from Contacts (people v1)", async () => [(await text(birthday)).includes('In 2 days · turns'), await text(birthday)]);
  check((await text('next-title')) === "days until Priya Shah's birthday", 'and is next up', await text('next-title'));
  await ui.getByTestId(birthday).click(LONG_PRESS);
  await ui.getByTestId(`${birthday}-action-0`).click();
  await until('hiding the birthday removes it', async () => [(await ui.getByTestId(birthday).count()) === 0 && (await text('show-hidden')).includes('Show 1 hidden birthday'), await text('show-hidden')]);
  await ui.getByTestId('show-hidden').click();
  await ui.getByTestId(birthday).waitFor({ timeout: UI_TIMEOUT });
  ok('"Show 1 hidden birthday" brings it back');

  await context.close();
  console.log('PASS: money and health templates smoke');
} finally {
  await browser.close();
}
