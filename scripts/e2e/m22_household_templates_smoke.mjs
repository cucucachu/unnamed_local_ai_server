// M22-16: the household templates in the browser. Driven by
// m22_household_templates_smoke.sh (which makes the users and cleans up);
// see its header for what is checked.
//
// Env: HOUSEHOLD_SMOKE_USER / _EDITOR and their _PASSWORDs (e2e-* users),
// HOUSEHOLD_SMOKE_SPACE_SLUG (e2e-household-*), HOUSEHOLD_SMOKE_STATE (a file
// this writes the app ids to, for the cleanup trap), HOUSEHOLD_SMOKE_BASE_URL
// (default http://localhost/).
import fs from 'node:fs';
import { chromium } from 'playwright';

import { loginThroughUi } from './auth_helpers.mjs';

const BASE = (process.env.HOUSEHOLD_SMOKE_BASE_URL ?? 'http://localhost/').replace(/\/$/, '');
const OWNER = { username: process.env.HOUSEHOLD_SMOKE_USER, password: process.env.HOUSEHOLD_SMOKE_PASSWORD };
const EDITOR = { username: process.env.HOUSEHOLD_SMOKE_EDITOR, password: process.env.HOUSEHOLD_SMOKE_EDITOR_PASSWORD };
const SLUG = process.env.HOUSEHOLD_SMOKE_SPACE_SLUG;
const STATE = process.env.HOUSEHOLD_SMOKE_STATE;
if (![OWNER, EDITOR].every((u) => /^e2e-/.test(u.username ?? '') && u.password) || !/^e2e-household-/.test(SLUG ?? '') || !STATE) {
  throw new Error('HOUSEHOLD_SMOKE_USER / _EDITOR (e2e-*), their passwords, HOUSEHOLD_SMOKE_SPACE_SLUG (e2e-household-*) and HOUSEHOLD_SMOKE_STATE are required');
}
const UI_TIMEOUT = 30_000;
const SEARCH_TIMEOUT = 120_000;
const BUILD_TIMEOUT = 240_000;
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

function api(context) {
  return async (method, url, { json, timeout = 30_000 } = {}) => {
    const r = await context.request.fetch(`${BASE}${url}`, { method, timeout, ...(json !== undefined ? { data: json } : {}) });
    const text = await r.text();
    if (!r.ok()) throw new Error(`${method} ${url}: ${r.status()} ${text}`);
    return text ? JSON.parse(text) : null;
  };
}

async function signIn(browser, user) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${BASE}/`);
  await loginThroughUi(page, user);
  return { context, page, call: api(context) };
}

const ui = (page) => page.frameLocator(APP_FRAME);
const text = (page, testID) => ui(page).getByTestId(testID).innerText({ timeout: 2000 });
const count = (page, testID) => ui(page).getByTestId(testID).count();

/** The testID of the first row in list `listID`. */
const firstRow = (page, listID, prefix) =>
  ui(page).getByTestId(listID).locator(`[data-testid^="${prefix}"]`).first().getAttribute('data-testid', { timeout: 2000 });

async function openApp(page, instance, readyTestID) {
  await page.goto(`${BASE}/apps/${instance.id}`);
  await page.getByTestId('app-runner').waitFor({ timeout: UI_TIMEOUT });
  await ui(page).getByTestId(readyTestID).waitFor({ timeout: UI_TIMEOUT });
}

/** Re-types `query` until `probe` holds: rows reach the index a moment after they're written. */
async function searchUntil(page, what, query, probe) {
  let typed = 0;
  await until(
    what,
    async () => {
      if (Date.now() - typed > 5000) {
        await ui(page).getByTestId('search').fill('');
        await ui(page).getByTestId('search').fill(query);
        typed = Date.now();
      }
      return probe();
    },
    SEARCH_TIMEOUT,
  );
}

const browser = await chromium.launch();
try {
  const owner = await signIn(browser, OWNER);
  ok(`signed in as ${OWNER.username}`);

  // --- setup over REST: shared space + editor, the five templates ----------
  const shared = await owner.call('POST', '/api/platform/spaces', { json: { slug: SLUG, name: `E2E Household ${process.pid}` } });
  const directory = (await owner.call('GET', '/api/platform/users/directory')).users;
  const userId = (u) => directory.find((d) => d.username === u.username).id;
  await owner.call('POST', `/api/platform/spaces/${shared.id}/members`, { json: { user_id: userId(EDITOR), role: 'editor' } });
  const installed = {};
  for (const slug of ['grocery-list', 'chores', 'pantry', 'recipes', 'contacts']) {
    const out = await owner.call('POST', `/api/platform/spaces/${shared.id}/app-templates/${slug}/install`, { json: {}, timeout: BUILD_TIMEOUT });
    fs.appendFileSync(STATE, `${out.app.id}\n`);
    check(out.build?.ok && out.build.diagnostics.length === 0, `installed and built ${slug} with zero diagnostics`, out.build);
    installed[slug] = out.instance;
  }
  const rows = (slug, sql, params = []) =>
    owner.call('POST', `/api/platform/apps/instances/${installed[slug].id}/rpc`, { json: { op: 'getAll', sql, params } }).then((r) => r.rows);

  const { page } = owner;
  const editor = await signIn(browser, EDITOR);
  ok(`signed in as ${EDITOR.username} (editor of ${SLUG})`);

  // --- Chores ----------------------------------------------------------------
  await openApp(page, installed.chores, 'due-empty');
  ok('Chores opens on an empty Due tab');
  await ui(page).getByTestId('new-chore').click();
  await ui(page).getByTestId('modal').waitFor({ timeout: UI_TIMEOUT });
  await ui(page).getByTestId('chore-name').fill('Take out the bins');
  await ui(page).getByTestId('chore-every').fill('7');
  await ui(page).getByTestId('chore-next-due').fill(TODAY);
  await ui(page).getByTestId(`chore-assignee-${EDITOR.username}`).click();
  await ui(page).getByTestId('chore-save').click();
  let chore;
  await until('the new chore is saved with both members in the rota, the owner first', async () => {
    [chore] = await rows('chores', 'SELECT id, every_days, next_due, turn FROM chores');
    const rota = chore ? await rows('chores', 'SELECT user_id FROM chore_assignees WHERE chore_id = ? ORDER BY position', [chore.id]) : [];
    return [JSON.stringify(rota.map((r) => r.user_id)) === JSON.stringify([userId(OWNER), userId(EDITOR)]), { chore, rota }];
  });
  await ui(page).getByTestId(`done-${chore.id}`).waitFor({ timeout: UI_TIMEOUT });
  check((await ui(page).locator('body').innerText()).includes('Your turn'), "it's due today and it's the owner's turn");

  await openApp(editor.page, installed.chores, `due-${chore.id}`);
  check(!(await ui(editor.page).locator('body').innerText()).includes('Your turn'), "the editor sees it, but not as their turn");

  await ui(page).getByTestId(`done-${chore.id}`).click();
  await until('Done reports it', async () => [(await text(page, 'due-message')) === 'Done: Take out the bins', await text(page, 'due-message')]);
  await ui(page).getByTestId('due-empty').waitFor({ timeout: UI_TIMEOUT });
  const [afterOwner] = await rows('chores', 'SELECT next_due, turn FROM chores');
  const done1 = await rows('chores', 'SELECT id, done_on, _created_by FROM completions');
  check(
    afterOwner.next_due === inDays(7) && afterOwner.turn === 1 && done1.length === 1 && done1[0]._created_by === userId(OWNER) && done1[0].done_on === TODAY,
    'completeChore logged it as the owner, moved it a week on and passed the turn',
    { afterOwner, done1 },
  );

  await ui(page).getByTestId('tab-history').click();
  await ui(page).getByTestId(`done-row-${done1[0].id}`).waitFor({ timeout: UI_TIMEOUT });
  ok('the History tab lists it');

  await ui(page).getByTestId('tab-all').click();
  await ui(page).getByTestId(`chore-${chore.id}`).click();
  await ui(page).getByTestId('chore-status').waitFor({ timeout: UI_TIMEOUT });
  await ui(page).getByTestId('chore-every').fill('3');
  await ui(page).getByTestId('chore-next-due').fill(TODAY);
  await ui(page).getByTestId('chore-save').click();
  await until('editing it saves how often and when, and keeps the rota', async () => {
    const [c] = await rows('chores', 'SELECT every_days, next_due, turn FROM chores');
    const rota = await rows('chores', 'SELECT user_id FROM chore_assignees ORDER BY position');
    return [c.every_days === 3 && c.next_due === TODAY && c.turn === 1 && rota.length === 2, { c, rota }];
  });

  await until('the editor now sees it as their turn without reloading', async () => {
    const body = await ui(editor.page).locator('body').innerText();
    return [body.includes('Your turn') && (await count(editor.page, `done-${chore.id}`)) === 1, body];
  });
  await ui(editor.page).getByTestId(`done-${chore.id}`).click();
  await until("the editor's Done is logged as the editor and the turn goes back to the owner", async () => {
    const done = await rows('chores', 'SELECT _created_by FROM completions ORDER BY id');
    const [c] = await rows('chores', 'SELECT next_due, turn FROM chores');
    return [done.length === 2 && done[1]._created_by === userId(EDITOR) && c.turn === 0 && c.next_due === inDays(3), { done, c }];
  });

  // --- Pantry ----------------------------------------------------------------
  const addPantry = async ({ name, quantity, unit, location, expires }) => {
    await ui(page).getByTestId('new-item').click();
    await ui(page).getByTestId('modal').waitFor({ timeout: UI_TIMEOUT });
    await ui(page).getByTestId('item-name').fill(name);
    await ui(page).getByTestId('item-quantity').fill(String(quantity));
    await ui(page).getByTestId('item-unit').fill(unit);
    await ui(page).getByTestId(`item-location-${location}`).click();
    if (expires) {
      await ui(page).getByTestId('item-expires').click();
      await ui(page).getByTestId('item-expires-on').fill(expires);
    }
    await ui(page).getByTestId('item-save').click();
    await ui(page).getByTestId('modal').waitFor({ state: 'detached', timeout: UI_TIMEOUT });
  };
  await openApp(page, installed.pantry, 'pantry-empty');
  ok('Pantry opens empty');
  await addPantry({ name: 'Basmati rice', quantity: 2, unit: 'kg', location: 'Pantry' });
  await addPantry({ name: 'Milk', quantity: 1, unit: 'L', location: 'Fridge', expires: inDays(2) });
  await addPantry({ name: 'Tinned tomatoes', quantity: 4, unit: 'cans', location: 'Pantry' });
  await addPantry({ name: 'basmati rice', quantity: 1, unit: 'kg', location: 'Pantry' });
  const pantry = async () => Object.fromEntries((await rows('pantry', 'SELECT id, name, quantity, location, expires_on FROM items')).map((r) => [r.name, r]));
  let items = await pantry();
  check(
    Object.keys(items).length === 3 && items['Basmati rice'].quantity === 3 && items.Milk.expires_on === inDays(2),
    'adding the same rice again topped it up (addItem); Milk has its best-before',
    items,
  );
  await until('Milk is under "Use soon"', async () => [(await ui(page).getByTestId('soon').getByTestId(`item-${items.Milk.id}`).count()) === 1, null]);
  await ui(page).getByTestId('place-Fridge').click();
  await until('the Fridge chip shows only the fridge', async () => {
    const n = await ui(page).getByTestId('items').getByTestId(`item-${items['Basmati rice'].id}`).count();
    return [n === 0 && (await ui(page).getByTestId('items').getByTestId(`item-${items.Milk.id}`).count()) === 1, n];
  });
  await ui(page).getByTestId('place-all').click();

  await ui(page).getByTestId('items').getByTestId(`item-${items.Milk.id}`).click();
  await until('the item screen says how much and where', async () => [(await text(page, 'item-status')).startsWith('1 L in the Fridge'), await text(page, 'item-status')]);
  await ui(page).getByTestId('item-use').click();
  await ui(page).getByTestId('pantry-empty').or(ui(page).getByTestId('items')).first().waitFor({ timeout: UI_TIMEOUT });
  await until('"Use it up" deletes the last of it (useSome) and goes back', async () => [!(await pantry()).Milk, await pantry()]);

  await ui(page).getByTestId(`item-${items['Basmati rice'].id}`).click();
  await ui(page).getByTestId('item-quantity').fill('2.5');
  await ui(page).getByTestId('item-save').click();
  await until('editing the quantity saves it', async () => [(await pantry())['Basmati rice']?.quantity === 2.5, await pantry()]);

  items = await pantry();
  // Unsearched, the rice comes first (A-Z); search() ranks by relevance.
  await searchUntil(page, 'searching "tomatoes" puts the tinned tomatoes first', 'tomatoes', async () => {
    const first = await firstRow(page, 'items', 'item-');
    return [first === `item-${items['Tinned tomatoes'].id}`, first];
  });

  // --- Recipes -> Grocery list -------------------------------------------------
  const exporters = await rows('recipes', 'SELECT _instance, _space, app, export FROM homeai_exports');
  check(
    exporters.length === 1 && exporters[0]._instance === installed['grocery-list'].id && exporters[0]._space === `/spaces/${SLUG}`,
    'Recipes sees the space\'s grocery list in homeai_exports',
    exporters,
  );
  await openApp(page, installed.recipes, 'recipes-empty');
  ok('Recipes opens empty');
  await ui(page).getByTestId('new-recipe').click();
  await ui(page).getByTestId('modal').waitFor({ timeout: UI_TIMEOUT });
  await ui(page).getByTestId('recipe-name').fill('Dal tadka');
  await ui(page).getByTestId('recipe-cuisine').fill('Indian');
  await ui(page).getByTestId('recipe-servings').fill('4');
  await ui(page).getByTestId('recipe-minutes').fill('40');
  await ui(page).getByTestId('recipe-ingredients').fill('- 200 g red lentils\n- 1 onion\n\n* 2 tomatoes');
  await ui(page).getByTestId('recipe-steps').fill('Simmer the lentils.\nFry the spices and stir them in.');
  await ui(page).getByTestId('recipe-save').click();
  await until('saving opens the new recipe', async () => [(await text(page, 'recipe-summary')) === 'Indian · serves 4 · 40 min', await text(page, 'recipe-summary')]);
  check((await text(page, 'ingredient-2')) === '• 2 tomatoes' && (await count(page, 'ingredient-3')) === 0, 'it lists three ingredients, without the markers');
  const [recipe] = await rows('recipes', 'SELECT id FROM recipes');

  await ui(page).getByTestId('grocery-add').click();
  await until('"Add ingredients" reports three', async () => [(await text(page, 'grocery-message')) === 'Added 3 items to the grocery list.', await text(page, 'grocery-message')]);
  const groceries = () => rows('grocery-list', 'SELECT name, checked FROM items ORDER BY id');
  check(
    JSON.stringify((await groceries()).map((r) => r.name)) === JSON.stringify(['200 g red lentils', '1 onion', '2 tomatoes']),
    "they're on the grocery list (its addItem, as an export action)",
    await groceries(),
  );
  await ui(page).getByTestId('grocery-add').click();
  await page.waitForTimeout(1500);
  check((await groceries()).length === 3, 'adding them again does not duplicate them', await groceries());

  await ui(page).getByTestId('recipe-edit').click();
  await ui(page).getByTestId('recipe-minutes').fill('35');
  await ui(page).getByTestId('recipe-save').click();
  await until('editing it goes back to the recipe with the new time', async () => [(await text(page, 'recipe-summary')) === 'Indian · serves 4 · 35 min', await text(page, 'recipe-summary')]);

  await ui(page).getByTestId('recipe-plan').click();
  await ui(page).getByTestId('modal').waitFor({ timeout: UI_TIMEOUT });
  await ui(page).getByTestId('plan-slot').selectOption({ label: 'Dinner' });
  await ui(page).getByTestId('plan-save').click();
  await until("planning it from the recipe puts it on today's dinner (planMeal)", async () => {
    const meals = await rows('recipes', 'SELECT day, slot, recipe_id, note FROM meals');
    return [meals.length === 1 && meals[0].day === TODAY && meals[0].slot === 'Dinner' && meals[0].recipe_id === recipe.id, meals];
  });
  await ui(page).getByTestId('modal').waitFor({ state: 'detached', timeout: UI_TIMEOUT });
  await ui(page).getByTestId('header-back').click();
  await ui(page).getByTestId('tab-plan').click();
  await until('the Meal plan tab shows it', async () => [(await text(page, `meal-${TODAY}-Dinner`)).includes('Dal tadka'), await text(page, `meal-${TODAY}-Dinner`)]);
  await ui(page).getByTestId(`plan-${TODAY}`).click();
  await ui(page).getByTestId('plan-note').fill('Leftovers');
  await ui(page).getByTestId('plan-save').click();
  await until("a note-only dinner replaces it", async () => {
    const meals = await rows('recipes', 'SELECT slot, recipe_id, note FROM meals WHERE day = ?', [TODAY]);
    return [meals.length === 1 && meals[0].recipe_id === null && meals[0].note === 'Leftovers', meals];
  });
  await until('and the plan shows the note', async () => [(await text(page, `meal-${TODAY}-Dinner`)).includes('Leftovers'), await text(page, `meal-${TODAY}-Dinner`)]);

  await openApp(page, installed['grocery-list'], 'summary');
  await until('the Grocery list app shows the three ingredients', async () => [(await text(page, 'summary')) === '3 to buy, 0 checked', await text(page, 'summary')]);

  // --- Contacts --------------------------------------------------------------
  /** Fills the New / Edit form: plain fields, then entries appended after `from` existing ones. */
  const fillPerson = async (f, from = {}) => {
    for (const [key, testID] of [['name', 'person-name'], ['nickname', 'person-nickname'], ['company', 'person-company'], ['job_title', 'person-job-title'], ['birthday', 'person-birthday'], ['notes', 'person-notes']]) {
      if (f[key] !== undefined) await ui(page).getByTestId(testID).fill(f[key]);
    }
    for (const kind of ['phone', 'email', 'website']) {
      for (const [n, [label, value]] of (f[`${kind}s`] ?? []).entries()) {
        const i = (from[kind] ?? 0) + n;
        await ui(page).getByTestId(`${kind}-add`).click();
        await ui(page).getByTestId(`${kind}-${i}-label`).selectOption({ label });
        await ui(page).getByTestId(`${kind}-${i}-value`).fill(value);
      }
    }
    for (const [n, address] of (f.addresses ?? []).entries()) {
      const i = (from.address ?? 0) + n;
      await ui(page).getByTestId('address-add').click();
      for (const [key, value] of Object.entries(address)) {
        if (key === 'label') await ui(page).getByTestId(`address-${i}-label`).selectOption({ label: value });
        else await ui(page).getByTestId(`address-${i}-${key}`).fill(value);
      }
    }
    await ui(page).getByTestId('person-save').click();
  };
  const addPerson = async (f) => {
    await ui(page).getByTestId('new-person').click();
    await ui(page).getByTestId('modal').waitFor({ timeout: UI_TIMEOUT });
    await fillPerson(f);
    await ui(page).getByTestId('person-delete').waitFor({ timeout: UI_TIMEOUT });
  };
  const people = () => rows('contacts', 'SELECT id, name, nickname, company, job_title, birthday, notes FROM people ORDER BY id');
  const details = () => rows('contacts', 'SELECT person_id, kind, label, value FROM contact_details ORDER BY id');
  const addresses = () => rows('contacts', 'SELECT id, person_id, label, street1, street2, city, region, postcode, country FROM addresses ORDER BY id');
  const websites = () => rows('contacts', 'SELECT person_id, label, url FROM websites ORDER BY id');

  await openApp(page, installed.contacts, 'people-empty');
  ok('Contacts opens empty');
  const birthday = `1990-${inDays(5).slice(5)}`;
  await addPerson({
    name: 'Priya Shah', nickname: 'Pree', company: 'Northwind Dental', job_title: 'Dentist', birthday, notes: 'Likes pottery',
    phones: [['Mobile', '555-0101'], ['Work', '555-0102']],
    emails: [['Home', 'priya@example.com'], ['Work', 'priya@northwind.example']],
    addresses: [{ label: 'Home', street1: '12 Elm St', street2: 'Apt 3', city: 'Springfield', region: 'OR', postcode: '97477', country: 'USA' }],
  });
  await until('a new contact opens with their next birthday', async () => [(await text(page, 'person-next-birthday')).includes('In 5 days'), await text(page, 'person-next-birthday')]);
  await until('the New form saved two phones, two emails and a structured address (savePerson)', async () => {
    const [ps, ds, as] = [await people(), await details(), await addresses()];
    return [
      ps.length === 1 && ps[0].nickname === 'Pree' && ps[0].company === 'Northwind Dental' && ps[0].job_title === 'Dentist' &&
        JSON.stringify(ds.map((d) => [d.kind, d.label, d.value])) === JSON.stringify([['phone', 'mobile', '555-0101'], ['phone', 'work', '555-0102'], ['email', 'home', 'priya@example.com'], ['email', 'work', 'priya@northwind.example']]) &&
        as.length === 1 && as[0].street2 === 'Apt 3' && as[0].postcode === '97477' && as[0].label === 'home',
      { ps, ds, as },
    ];
  });
  const priya = (await people())[0];
  const home = (await addresses())[0];
  await until('the address shows formatted', async () => {
    const shown = await text(page, `address-a${home.id}-text`);
    return [shown === '12 Elm St\nApt 3\nSpringfield, OR 97477\nUSA', shown];
  });
  await until('the contact shows their work', async () => [(await text(page, 'person-work')) === 'Dentist · Northwind Dental', await text(page, 'person-work')]);

  await ui(page).getByTestId('person-edit').click();
  await ui(page).getByTestId('person-save').waitFor({ timeout: UI_TIMEOUT });
  await ui(page).getByTestId('phone-1-remove').click();
  await ui(page).getByTestId('address-0-city').fill('Eugene');
  await fillPerson({ notes: 'Likes pottery and bouldering', websites: [['Personal', 'priya.example.com']] });
  await ui(page).getByTestId('person-edit').waitFor({ timeout: UI_TIMEOUT });
  await until('editing saves the notes, drops a phone, changes the city and adds a website', async () => {
    const [ps, ds, as, ws] = [await people(), await details(), await addresses(), await websites()];
    return [
      ps[0]?.notes === 'Likes pottery and bouldering' && ds.filter((d) => d.kind === 'phone').length === 1 && ds.length === 3 &&
        as.length === 1 && as[0].city === 'Eugene' && as[0].street1 === '12 Elm St' && ws.length === 1 && ws[0].url === 'priya.example.com',
      { ps, ds, as, ws },
    ];
  });

  await ui(page).getByTestId('header-back').click();
  await addPerson({ name: 'Priya S.', notes: 'From the climbing gym', emails: [['Other', 'priya.s@example.com']] });
  await until('the duplicate has an email', async () => [(await details()).length === 4, await details()]);
  await ui(page).getByTestId('header-back').click();
  await addPerson({ name: 'Tom Baker', company: 'Baker & Sons', notes: 'Neighbour, has a ladder' });
  await ui(page).getByTestId('header-back').click();

  await ui(page).getByTestId(`birthday-${priya.id}`).waitFor({ timeout: UI_TIMEOUT });
  ok('"Coming up" lists Priya\'s birthday');
  await ui(page).getByTestId(`person-${priya.id}`).click();
  const dup = (await people()).find((p) => p.name === 'Priya S.');
  await ui(page).getByTestId('merge-pick').selectOption({ label: dup.name });
  await ui(page).getByTestId('merge').click();
  await until('merging reports it', async () => [(await text(page, 'person-message')) === 'Merged Priya S. into Priya Shah.', await text(page, 'person-message')]);
  await until('the duplicate is gone, its email and notes moved over (mergePeople)', async () => {
    const ps = await people();
    const ds = await details();
    const kept = ps.find((p) => p.id === priya.id);
    return [
      ps.length === 2 && ds.every((d) => d.person_id === priya.id) && ds.length === 4 && kept.notes.includes('climbing gym') && kept.birthday === birthday && kept.nickname === 'Pree',
      { ps, ds },
    ];
  });

  await ui(page).getByTestId('header-back').click();
  const tom = (await people()).find((p) => p.name === 'Tom Baker');
  // Unsearched, Priya comes first (A-Z).
  await searchUntil(page, 'searching "ladder" puts Tom first', 'ladder', async () => {
    const first = await firstRow(page, 'people', 'person-');
    return [first === `person-${tom.id}`, first];
  });
  await searchUntil(page, 'searching a company ("Baker & Sons") finds Tom', 'Baker & Sons', async () => {
    const first = await firstRow(page, 'people', 'person-');
    return [first === `person-${tom.id}`, first];
  });
  await searchUntil(page, 'searching a nickname ("Pree") finds Priya', 'Pree', async () => {
    const first = await firstRow(page, 'people', 'person-');
    return [first === `person-${priya.id}`, first];
  });

  await editor.context.close();
  await owner.context.close();
  console.log('PASS: household templates smoke');
} finally {
  await browser.close();
}
