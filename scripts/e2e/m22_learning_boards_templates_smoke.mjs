// M22-18: the flashcards, kanban, books and calculator templates in the
// browser. Driven by m22_learning_boards_templates_smoke.sh (which makes the
// user and cleans up); see its header for what is checked.
//
// Env: BOARDS_SMOKE_USER / BOARDS_SMOKE_PASSWORD (an e2e-* user),
// BOARDS_SMOKE_STATE (a file this writes the app ids to, for the cleanup
// trap), BOARDS_SMOKE_BASE_URL (default http://localhost/).
import fs from 'node:fs';
import { chromium } from 'playwright';

import { loginThroughUi } from './auth_helpers.mjs';

const BASE = (process.env.BOARDS_SMOKE_BASE_URL ?? 'http://localhost/').replace(/\/$/, '');
const USER = { username: process.env.BOARDS_SMOKE_USER, password: process.env.BOARDS_SMOKE_PASSWORD };
const STATE = process.env.BOARDS_SMOKE_STATE;
if (!/^e2e-/.test(USER.username ?? '') || !USER.password || !STATE) {
  throw new Error('BOARDS_SMOKE_USER (e2e-*), BOARDS_SMOKE_PASSWORD and BOARDS_SMOKE_STATE are required');
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

const browser = await chromium.launch();
try {
  const context = await browser.newContext({ locale: 'en-US' });
  const page = await context.newPage();
  const call = async (method, url, { json, timeout = 30_000 } = {}) => {
    const r = await context.request.fetch(`${BASE}${url}`, { method, timeout, ...(json !== undefined ? { data: json } : {}) });
    const text = await r.text();
    if (!r.ok()) throw new Error(`${method} ${url}: ${r.status()} ${text}`);
    return text ? JSON.parse(text) : null;
  };
  await page.goto(`${BASE}/`);
  await loginThroughUi(page, USER);
  ok(`signed in as ${USER.username}`);

  const personal = (await call('GET', '/api/platform/spaces')).spaces.find((s) => s.kind === 'personal');
  const installed = {};
  for (const slug of ['flashcards', 'kanban', 'books', 'calculator']) {
    const out = await call('POST', `/api/platform/spaces/${personal.id}/app-templates/${slug}/install`, { json: {}, timeout: BUILD_TIMEOUT });
    fs.appendFileSync(STATE, `${out.app.id}\n`);
    check(out.build?.ok && out.build.diagnostics.length === 0, `installed and built ${slug} with zero diagnostics`, out.build);
    installed[slug] = out.instance;
  }
  const rpc = (slug, body) => call('POST', `/api/platform/apps/instances/${installed[slug].id}/rpc`, { json: body });
  const rows = async (slug, sql, params = []) => (await rpc(slug, { op: 'getAll', sql, params })).rows;

  const ui = page.frameLocator(APP_FRAME);
  const text = (testID) => ui.getByTestId(testID).innerText({ timeout: 2000 });
  const label = (testID) => ui.getByTestId(testID).getAttribute('aria-label', { timeout: 2000 });
  const firstRow = async (prefix) => (await ui.locator(`[data-testid^="${prefix}"]`).first().getAttribute('data-testid', { timeout: 2000 })).slice(prefix.length);
  const openApp = async (slug, readyTestID) => {
    await page.goto(`${BASE}/apps/${installed[slug].id}`);
    await page.getByTestId('app-runner').waitFor({ timeout: UI_TIMEOUT });
    await ui.getByTestId(readyTestID).waitFor({ timeout: UI_TIMEOUT });
  };
  const modalClosed = () => ui.getByTestId('modal').waitFor({ state: 'detached', timeout: UI_TIMEOUT });

  // --- Flashcards --------------------------------------------------------------
  await openApp('flashcards', 'decks-empty');
  ok('Flashcards opens with no decks');
  await ui.getByTestId('deck-name').fill('Spanish');
  await ui.getByTestId('deck-add').click();
  await ui.getByTestId('deck-study').waitFor({ timeout: UI_TIMEOUT });
  check((await text('deck-study')).includes('Nothing due today'), 'a new deck opens with nothing to study');
  for (const [front, back] of [
    ['comer', 'to eat'],
    ['beber', 'to drink'],
    ['vivir', 'to live'],
  ]) {
    await ui.getByTestId('card-front').fill(front);
    await ui.getByTestId('card-back').fill(back);
    await ui.getByTestId('card-add').click();
    await until(`card "${front}" is added`, async () => [(await rows('flashcards', 'SELECT id FROM cards WHERE front = ?', [front])).length === 1, null]);
  }
  const card = Object.fromEntries((await rows('flashcards', 'SELECT id, front FROM cards')).map((c) => [c.front, c.id]));
  const sched = async (front) => (await rows('flashcards', 'SELECT due, interval_days, reps, lapses FROM cards WHERE id = ?', [card[front]]))[0];
  await until('three new cards are due', async () => [(await text('deck-study')).includes('Study 3 due'), await text('deck-study')]);

  const study = async (front, grade) => {
    await until(`the session shows "${front}"`, async () => [(await text('study-front')) === front, await text('study-front')]);
    await ui.getByTestId('study-show').click();
    await ui.getByTestId(`grade-${grade}`).waitFor({ timeout: UI_TIMEOUT });
  };
  await ui.getByTestId('deck-study').click();
  await study('comer', 2);
  check((await text('study-back')) === 'to eat', 'Show answer reveals the back');
  const labels = await Promise.all([0, 1, 2, 3].map((g) => text(`grade-${g}`)));
  check(labels[0].includes('today') && labels[1].includes('1 day') && labels[2].includes('1 day') && labels[3].includes('4 days'), 'a new card offers today / 1 day / 1 day / 4 days', labels);
  await ui.getByTestId('grade-2').click();
  await until('Good on a new card schedules it for tomorrow', async () => {
    const s = await sched('comer');
    return [s.due === inDays(1) && s.interval_days === 1 && s.reps === 1, s];
  });
  await study('beber', 0);
  await ui.getByTestId('grade-0').click();
  await until('Again keeps it due today', async () => {
    const s = await sched('beber');
    return [s.due === TODAY && s.interval_days === 0 && s.reps === 0, s];
  });
  await study('vivir', 3);
  await ui.getByTestId('grade-3').click();
  await until('Easy on a new card is four days out', async () => [(await sched('vivir')).due === inDays(4), await sched('vivir')]);
  await study('beber', 2);
  await ui.getByTestId('grade-2').click();
  await until('the card graded Again comes back last and then passes', async () => [(await sched('beber')).due === inDays(1), await sched('beber')]);
  await until('the session ends with the day\'s count', async () => [(await text('study-done')).includes('4 reviews today'), await text('study-done')]);
  await ui.getByTestId('study-done').getByRole('button', { name: 'Close' }).click();
  await modalClosed();

  // A card two reviews in, 3 days apart: Good should be round(3 × 2.5) = 8 days, on the button and in SQL.
  await rpc('flashcards', { op: 'run', sql: 'UPDATE cards SET due = ?, interval_days = 3, reps = 2 WHERE id = ?', params: [TODAY, card.comer] });
  await ui.getByTestId('deck-study').click();
  await study('comer', 2);
  check((await text('grade-2')).includes('8 days'), 'the Good button says 8 days for a 3-day card', await text('grade-2'));
  await ui.getByTestId('grade-2').click();
  await until('and grade.sql agrees (8 days)', async () => {
    const s = await sched('comer');
    return [s.interval_days === 8 && s.due === inDays(8), s];
  });
  await ui.getByTestId('study-done').waitFor({ timeout: UI_TIMEOUT });
  await ui.getByTestId('study-done').getByRole('button', { name: 'Close' }).click();
  await modalClosed();

  await ui.getByTestId(`card-${card.vivir}`).click();
  check((await text('card-schedule')).includes('Due in 4 days'), 'a card shows its schedule', await text('card-schedule'));
  await ui.getByTestId('card-reset').click();
  await until('"Start this card over" makes it new again', async () => {
    const s = await sched('vivir');
    return [s.due === TODAY && s.interval_days === 0 && (await text('card-schedule')).startsWith('New'), { s, label: await text('card-schedule') }];
  });
  await ui.getByTestId('card-edit-back').fill('to live (vivir)');
  await ui.getByTestId('card-save').click();
  await until('editing a card saves it', async () => [(await rows('flashcards', 'SELECT back FROM cards WHERE id = ?', [card.vivir]))[0].back === 'to live (vivir)', null]);

  await page.goto(`${BASE}/apps/${installed.flashcards.id}`);
  await ui.getByTestId('cards-search').waitFor({ timeout: UI_TIMEOUT });
  await ui.getByTestId('cards-search').fill('drink');
  await until('search ranks the matching card first', async () => [(await firstRow('hit-')) === String(card.beber), await firstRow('hit-')]);
  await ui.getByTestId('cards-search').fill('');
  await ui.getByTestId('tab-stats').click();
  await until('Progress has the totals', async () => [(await text('stat-cards')).includes('3') && (await text('stat-remembered')).includes('80%'), [await text('stat-cards'), await text('stat-remembered')]]);
  check((await label('reviews-chart')).startsWith('Reviews per day: bar chart'), 'the reviews chart is there', await label('reviews-chart'));

  // --- Kanban ----------------------------------------------------------------
  await openApp('kanban', 'boards-empty');
  ok('Kanban opens with no boards');
  await ui.getByTestId('board-name').fill('House');
  await ui.getByTestId('board-add').click();
  await until('Add board opens the new board', async () => [(await rows('kanban', 'SELECT id FROM boards')).length === 1 && (await ui.getByTestId('board-columns').count()) === 1, null]);
  const [board] = await rows('kanban', 'SELECT id FROM boards');
  const cols = await rows('kanban', 'SELECT id, name, wip_limit FROM columns WHERE board_id = ? ORDER BY position', [board.id]);
  check(cols.map((c) => c.name).join(',') === 'To do,Doing,Done' && cols[1].wip_limit === 3, 'a new board has To do, Doing (limit 3) and Done', cols);
  const [todo, doing, done] = cols.map((c) => c.id);
  const addCard = async (col, title) => {
    await ui.getByTestId(`add-${col}`).fill(title);
    await ui.getByTestId(`add-${col}-save`).click();
    await until(`"${title}" is added`, async () => [(await rows('kanban', 'SELECT id FROM cards WHERE title = ?', [title])).length === 1, null]);
  };
  await ui.getByTestId(`column-${todo}`).waitFor({ timeout: UI_TIMEOUT });
  for (const title of ['Fix the gate', 'Paint the fence', 'Clean gutters']) await addCard(todo, title);
  const kcard = Object.fromEntries((await rows('kanban', 'SELECT id, title FROM cards')).map((c) => [c.title, c.id]));
  const layout = async () =>
    Object.fromEntries(
      (await rows('kanban', "SELECT column_id, group_concat(title || '@' || position, ', ') AS cards FROM (SELECT * FROM cards ORDER BY column_id, position) GROUP BY column_id")).map((r) => [r.column_id, r.cards]),
    );

  // A real swipe: drag the row left with the mouse, then press the revealed action.
  const row = ui.getByTestId(`card-${kcard['Fix the gate']}`);
  const box = await row.boundingBox();
  await page.mouse.move(box.x + box.width - 20, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + 40, box.y + box.height / 2, { steps: 12 });
  await page.mouse.up();
  await until('swiping a card left reveals its actions', async () => [(await ui.getByTestId(`card-${kcard['Fix the gate']}-action-0`).getAttribute('aria-hidden')) !== 'true', null]);
  await ui.getByTestId(`card-${kcard['Fix the gate']}-action-0`).click();
  await until('its first action moves it on to Doing, and To do closes the gap', async () => {
    const l = await layout();
    return [l[doing] === 'Fix the gate@0' && l[todo] === 'Paint the fence@0, Clean gutters@1', l];
  });
  await ui.getByTestId(`card-${kcard['Paint the fence']}`).click(LONG_PRESS);
  await ui.getByTestId(`card-${kcard['Paint the fence']}-action-0`).click();
  await until('a long-press does the same (Paint the fence to Doing, at the bottom)', async () => [(await layout())[doing] === 'Fix the gate@0, Paint the fence@1', await layout()]);
  await addCard(doing, 'Order paint');
  await addCard(doing, 'Call plumber');
  const columnTitle = async (id) => (await text(`column-${id}-title`)).toLowerCase();
  await until('a column over its limit says so', async () => [(await columnTitle(doing)) === 'doing · 4 of 3 · over the limit', await columnTitle(doing)]);

  const plumber = (await rows('kanban', "SELECT id FROM cards WHERE title = 'Call plumber'"))[0].id;
  await ui.getByTestId(`card-${plumber}`).click();
  await ui.getByTestId('card-up').click();
  await until('Move up swaps it with the card above', async () => [(await layout())[doing] === 'Fix the gate@0, Paint the fence@1, Call plumber@2, Order paint@3', await layout()]);
  await ui.getByTestId('card-note').fill('The kitchen tap drips');
  await ui.getByTestId('card-due').fill(inDays(-1));
  await ui.getByTestId('card-assignee').selectOption({ index: 1 });
  await ui.getByTestId('card-save').click();
  await until('a card saves its note, due date and who is doing it', async () => {
    const [c] = await rows('kanban', 'SELECT note, due, assignee FROM cards WHERE id = ?', [plumber]);
    return [c.note === 'The kitchen tap drips' && c.due === inDays(-1) && !!c.assignee, c];
  });
  await until('an overdue card says so on the board', async () => [(await text(`card-${plumber}`)).includes('Overdue'), await text(`card-${plumber}`)]);

  await ui.getByTestId(`card-${kcard['Fix the gate']}`).click(LONG_PRESS);
  await ui.getByTestId(`card-${kcard['Fix the gate']}-action-1`).click();
  await until('from the middle column, the second action moves it to Done', async () => [(await layout())[done] === 'Fix the gate@0' && (await layout())[doing] === 'Paint the fence@0, Call plumber@1, Order paint@2', await layout()]);
  await ui.getByTestId(`card-${kcard['Clean gutters']}`).click(LONG_PRESS);
  await ui.getByTestId(`card-${kcard['Clean gutters']}-action-1`).click();
  await until('the last action deletes it', async () => [(await rows('kanban', 'SELECT id FROM cards WHERE id = ?', [kcard['Clean gutters']])).length === 0, null]);

  await ui.getByTestId('board-columns').click();
  await ui.getByTestId('column-name').fill('Waiting');
  await ui.getByTestId('column-add').click();
  await until('a column is added at the end', async () => [(await rows('kanban', "SELECT position FROM columns WHERE name = 'Waiting'"))[0]?.position === 3, null]);
  const waiting = (await rows('kanban', "SELECT id FROM columns WHERE name = 'Waiting'"))[0].id;
  await ui.getByTestId(`col-${waiting}-left`).click();
  await until('moving it left puts it before Done', async () => [(await rows('kanban', 'SELECT group_concat(name) AS n FROM (SELECT name FROM columns ORDER BY position)'))[0].n === 'To do,Doing,Waiting,Done', null]);
  await ui.getByTestId(`col-${doing}-limit`).fill('5');
  await ui.getByTestId(`col-${doing}-save`).click();
  await until('the limit saves', async () => [(await rows('kanban', 'SELECT wip_limit FROM columns WHERE id = ?', [doing]))[0].wip_limit === 5, null]);
  await ui.getByTestId(`col-${waiting}-delete`).click();
  await until('deleting a column closes the gap', async () => [(await rows('kanban', 'SELECT group_concat(name || position) AS n FROM (SELECT name, position FROM columns ORDER BY position)'))[0].n === 'To do0,Doing1,Done2', null]);
  await ui.locator('[data-testid="header-back"]:visible').click();
  await until('the board is back under its limit', async () => [(await columnTitle(doing)) === 'doing · 3 of 5', await columnTitle(doing)]);
  await ui.locator('[data-testid="header-back"]:visible').click();
  await until('the boards list counts open cards', async () => [(await text(`board-${board.id}`)).includes('3 open of 4 cards'), await text(`board-${board.id}`)]);

  // --- Books and media ---------------------------------------------------------
  await openApp('books', 'shelf-empty');
  ok('Books and media opens on an empty shelf');
  const addItem = async ({ title, creator, kind, status, rating, notes }) => {
    await ui.getByTestId('modal').waitFor({ timeout: UI_TIMEOUT });
    await ui.getByTestId('item-title').fill(title);
    if (kind) await ui.getByTestId('item-kind').selectOption({ label: kind });
    await ui.getByTestId('item-creator').fill(creator);
    if (status) await ui.getByTestId('item-status').selectOption({ label: status });
    if (rating) await ui.getByTestId(`item-rating-${rating}`).click();
    if (notes) await ui.getByTestId('item-notes').fill(notes);
    await ui.getByTestId('item-save').click();
    await modalClosed();
  };
  await ui.getByRole('button', { name: 'Add one' }).click();
  await ui.getByTestId('item-title').waitFor({ timeout: UI_TIMEOUT });
  await ui.getByTestId('item-status').selectOption({ label: 'On it now' });
  check((await ui.getByTestId('item-started').inputValue()) === TODAY, 'picking "On it now" fills in today as the start');
  await addItem({ title: 'Piranesi', creator: 'Susanna Clarke', rating: 4 });
  await ui.getByTestId('new-item').click();
  await addItem({ title: 'Arrival', kind: 'Film', creator: 'Denis Villeneuve', status: 'Finished', rating: 5, notes: 'The linguist and the heptapods' });
  await ui.getByTestId('new-item').click();
  await addItem({ title: 'Outer Wilds', kind: 'Game', creator: 'Mobius Digital' });
  const items = Object.fromEntries((await rows('books', 'SELECT id, title FROM items')).map((i) => [i.title, i.id]));
  const item = async (title) => (await rows('books', 'SELECT kind, status, rating, started_on, finished_on FROM items WHERE id = ?', [items[title]]))[0];
  check((await item('Arrival')).kind === 'film' && (await item('Arrival')).finished_on === TODAY && (await item('Piranesi')).started_on === TODAY, 'the three are saved with their kinds and dates', [await item('Arrival'), await item('Piranesi')]);
  await until('the shelf groups them and words them per kind', async () => {
    const p = await ui.getByTestId('section-started').innerText({ timeout: 2000 });
    const a = await text(`item-${items.Arrival}`);
    const w = await text(`item-${items['Outer Wilds']}`);
    return [p.includes('Piranesi') && p.includes('Reading since') && a.includes('Watched') && a.includes('★★★★★') && w.includes('Want to play'), { p, a, w }];
  });

  await ui.getByTestId(`item-${items.Piranesi}`).click(LONG_PRESS);
  await ui.getByTestId(`item-${items.Piranesi}-action-0`).click();
  await until('swiping "Finished" finishes it today', async () => {
    const i = await item('Piranesi');
    return [i.status === 'finished' && i.finished_on === TODAY && i.started_on === TODAY, i];
  });
  await ui.getByTestId(`item-${items['Outer Wilds']}`).click(LONG_PRESS);
  await ui.getByTestId(`item-${items['Outer Wilds']}-action-0`).click();
  await until('swiping "Start" starts it today', async () => [(await item('Outer Wilds')).status === 'started' && (await item('Outer Wilds')).started_on === TODAY, await item('Outer Wilds')]);

  await ui.getByTestId('status-finished').click();
  await until('the Finished chip shows the two finished', async () => [(await ui.locator('[data-testid^="item-"]:not([data-testid*="-action-"])').count()) === 2, await ui.locator('[data-testid^="item-"]:not([data-testid*="-action-"])').count()]);
  await ui.getByTestId('kind-film').click();
  await until('and the Film chip narrows it to the film', async () => [(await ui.locator('[data-testid^="item-"]:not([data-testid*="-action-"])').count()) === 1 && (await ui.getByTestId(`item-${items.Arrival}`).count()) === 1, null]);
  await ui.getByTestId('kind-film').click();
  await ui.getByTestId('status-all').click();
  await ui.getByTestId('items-search').fill('heptapod');
  await until('search finds it by its notes', async () => [(await firstRow('item-')) === String(items.Arrival), await firstRow('item-')]);
  await ui.getByTestId('items-search').fill('');

  await ui.getByTestId(`item-${items['Outer Wilds']}`).click();
  await until('an item says where you are with it', async () => [(await text('item-status-label')).startsWith('Playing since'), await text('item-status-label')]);
  await ui.getByTestId('item-dropped').click();
  await until('"Gave up" stamps the day', async () => [(await item('Outer Wilds')).status === 'dropped' && (await item('Outer Wilds')).finished_on === TODAY && (await text('item-status-label')).startsWith('Gave up'), await item('Outer Wilds')]);
  await ui.getByTestId('item-status').selectOption({ label: 'Want to' });
  check((await ui.getByTestId('item-started').count()) === 0, 'back to "Want to" hides the dates');
  await ui.getByTestId('item-save').click();
  await until('and saving clears them', async () => {
    const i = await item('Outer Wilds');
    return [i.status === 'want' && i.started_on === null && i.finished_on === null, i];
  });

  await ui.getByTestId('tab-year').click();
  await until('This year counts by kind and averages the ratings', async () => [
    (await text('year-book')).includes('1') && (await text('year-film')).includes('1') && (await text('year-average')).includes('4.5'),
    [await text('year-book'), await text('year-film'), await text('year-average')],
  ]);
  check((await label('year-chart')).startsWith(`Finished per month in ${new Date().getFullYear()}: bar chart`), 'the year chart is there', await label('year-chart'));
  check((await firstRow('best-')) === String(items.Arrival), 'favorites start with the five-star one');

  // --- Calculator and units -----------------------------------------------------
  await openApp('calculator', 'key-equals');
  ok('Calculator and units opens');
  const keys = async (...ids) => {
    for (const id of ids) await ui.getByTestId(`key-${id}`).click();
  };
  await keys('1', '2', 'plus', '3', 'times', '4');
  await until('the answer shows as you type, × before +', async () => [(await text('calc-expression')) === '12+3×4' && (await text('calc-result')) === '= 24', [await text('calc-expression'), await text('calc-result')]]);
  await keys('equals');
  await until('= keeps the answer and adds it to Earlier', async () => [(await text('calc-expression')) === '24' && (await text('history-0')).includes('12+3×4'), await text('calc-expression')]);
  await keys('clear', 'open', '1', 'plus', '2', 'close', 'times', '3', 'equals');
  await until('parentheses', async () => [(await text('calc-expression')) === '9', await text('calc-expression')]);
  await keys('clear', '7', 'divide', '0', 'equals');
  await until('dividing by 0 is an error, not Infinity', async () => [(await text('calc-result')) === "Can't divide by 0", await text('calc-result')]);
  await keys('clear', '2', '0', '0', 'times', '1', '0', 'percent', 'back', 'percent');
  await until('% and ⌫', async () => [(await text('calc-expression')) === '200×10%' && (await text('calc-result')) === '= 20', [await text('calc-expression'), await text('calc-result')]]);
  await keys('clear', '0', 'dot', '1', 'plus', '0', 'dot', '2');
  await until('0.1 + 0.2 shows 0.3', async () => [(await text('calc-result')) === '= 0.3', await text('calc-result')]);

  await ui.getByTestId('tab-units').click();
  await ui.getByTestId('units-group-temperature').click();
  await ui.getByTestId('units-value').fill('100');
  await until('100 °C is 212 °F', async () => [(await text('units-result')) === '212', await text('units-result')]);
  await ui.getByTestId('units-swap').click();
  await until('swapped, 100 °F is 37.78 °C', async () => [(await text('units-result')).startsWith('37.777'), await text('units-result')]);
  await ui.getByTestId('units-group-volume').click();
  await ui.getByTestId('units-value').fill('1');
  await ui.getByTestId('units-from').selectOption({ label: 'US cups' });
  await ui.getByTestId('units-to').selectOption({ label: 'tablespoons (tbsp)' });
  await until('a cup is 16 tablespoons, and 236.6 ml in the list', async () => [(await text('units-result')) === '16' && (await text('units-all-ml')).includes('236.588'), [await text('units-result'), await text('units-all-ml')]]);

  await ui.getByTestId('tab-tip').click();
  await ui.getByTestId('tip-bill').fill('84.50');
  await ui.getByTestId('tip-20').click();
  await ui.getByTestId('tip-people-plus').click();
  await ui.getByTestId('tip-people-plus').click();
  await until('a 20% tip on $84.50 split three ways', async () => [
    (await text('tip-amount')).includes('$16.90') && (await text('tip-total')).includes('$101.40') && (await text('tip-each')).includes('$33.80'),
    [await text('tip-amount'), await text('tip-total'), await text('tip-each')],
  ]);
  await ui.getByTestId('tip-round').click();
  await until('rounding each share up puts the extra in the tip', async () => [
    (await text('tip-each')).includes('$34.00') && (await text('tip-total')).includes('$102.00') && (await text('tip-amount')).includes('$17.50'),
    [await text('tip-amount'), await text('tip-total'), await text('tip-each')],
  ]);

  await context.close();
  console.log('PASS: learning and boards templates smoke');
} finally {
  await browser.close();
}
