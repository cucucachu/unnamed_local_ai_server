// @homeai/ui and the theme (M22-03) in jsdom, with the kit-check fixture:
// the kit's inputs and rows drive app state, and the theme follows app.json's
// userInterfaceStyle and the host's scheme, leaving light apps as they were.
//   npm test    (builds dist/ first)
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { FIXTURE, bootJsdom, until } from './helpers.mjs';

const KIT = path.join(path.dirname(FIXTURE), 'kit-check');
const appJson = JSON.parse(fs.readFileSync(path.join(KIT, 'app.json'), 'utf8'));
const withStyle = (style) => ({ 'app.json': JSON.stringify({ ...appJson, userInterfaceStyle: style }) });

async function boot(opts = {}) {
  const s = await bootJsdom({ appDir: KIT, ...opts });
  const state = () => JSON.parse(s.$('state').textContent);
  /** Set an input's value the way typing or picking does, so React sees it. */
  const enter = (el, value) => {
    const proto = el.tagName === 'SELECT' ? s.win.HTMLSelectElement.prototype : el.tagName === 'TEXTAREA' ? s.win.HTMLTextAreaElement.prototype : s.win.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
    el.dispatchEvent(new s.win.Event('input', { bubbles: true }));
    el.dispatchEvent(new s.win.Event('change', { bubbles: true }));
  };
  const blur = (el) => el.dispatchEvent(new s.win.FocusEvent('focusout', { bubbles: true }));
  const color = (id) => s.win.getComputedStyle(s.$(id)).color;
  return { ...s, state, enter, blur, color };
}

test('the inputs drive app state', async () => {
  const s = await boot();
  await until('kit', () => s.shown('kit-screen'));
  assert.deepEqual(s.headings(), ['Kit', 'FIELDS', 'LIST', 'DISPLAY']);

  s.enter(s.$('name'), 'Ada');
  await until('name', () => s.state().name === 'Ada');
  assert.equal(s.win.document.querySelector('[role="alert"]'), null);
  s.enter(s.$('name'), 'bad');
  await until('error', () => s.win.document.querySelector('[role="alert"]')?.textContent === 'Not that one');

  // Typed as typed; clamped to max and rounded to one decimal on blur.
  s.enter(s.$('qty'), '12.34');
  await until('qty', () => s.state().qty === 12.34);
  s.blur(s.$('qty'));
  await until('clamped', () => s.state().qty === 10 && s.$('qty').value === '10.0');
  s.enter(s.$('qty'), '');
  await until('cleared', () => s.state().qty === null);

  assert.equal(s.$('date').type, 'date');
  s.enter(s.$('date'), '2026-10-18');
  s.enter(s.$('time'), '07:30');
  await until('date and time', () => s.state().date === '2026-10-18' && s.state().time === '07:30');
  s.enter(s.$('date'), '');
  await until('no date', () => s.state().date === null);

  assert.deepEqual([...s.$('size').options].map((o) => o.textContent), ['Choose…', 'Small', 'Large']);
  s.enter(s.$('size'), '1');
  await until('size', () => s.state().size === 'l');

  s.$('toggle').querySelector('input').click();
  await until('toggle', () => s.state().on === true);

  s.click('count-plus');
  await until('count', () => s.state().count === 2);
  s.click('count-plus');
  await until('count', () => s.state().count === 3);
  assert.equal(s.$('count').textContent, '3');
  assert.equal(s.$('count-plus').getAttribute('aria-disabled'), 'true', 'at max');
  s.click('count-minus');
  await until('count down', () => s.state().count === 2);
  assert.deepEqual(s.errors(), []);
  s.close();
});

test('rows: search, press, swipe actions and the empty state', async () => {
  const s = await boot();
  await until('kit', () => s.shown('kit-screen'));
  assert.equal(s.$('rows').getAttribute('role'), 'list');
  s.click('row-Eggs');
  await until('pressed', () => s.state().pressed === 'Eggs');

  s.enter(s.$('search'), 'mi');
  await until('filtered', () => !s.shown('row-Eggs') && s.shown('row-Milk'));
  s.click('search-clear');
  await until('cleared', () => s.state().query === '' && s.shown('row-Eggs'));

  // The action sits behind the row until a swipe or long press opens it.
  const del = s.$('row-Milk-action-0');
  assert.equal(del.getAttribute('aria-label'), 'Delete');
  assert.equal(del.getAttribute('aria-hidden'), 'true');
  del.click();
  await until('deleted', () => !s.shown('row-Milk'));
  s.click('row-Eggs-action-0');
  await until('empty', () => s.shown('empty'));
  assert.match(s.$('empty').textContent, /Nothing here.*Add something/);
  assert.equal(s.$('rows'), null);
  s.close();
});

test('display components and formatting', async () => {
  const s = await boot();
  await until('kit', () => s.shown('kit-screen'));
  assert.equal(s.$('progress').getAttribute('aria-valuenow'), '25');
  assert.equal(s.$('badge').textContent, '3');
  assert.match(s.$('money').textContent, /^-\$12\.50$/);
  assert.equal(s.$('duration').textContent, '12m 30s');
  assert.equal(s.$('chip').getAttribute('aria-pressed'), 'false');
  s.click('chip');
  await until('chip', () => s.state().chip === true);
  assert.equal(s.$('chip').getAttribute('aria-pressed'), 'true');
  s.click('save');
  await until('saved', () => s.state().pressed === 'save');
  assert.equal(s.$('busy').getAttribute('aria-disabled'), 'true', 'loading disables');
  s.click('busy');
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(s.state().pressed, 'save');
  s.close();
});

test('"automatic" follows the host scheme, from the config and then theme events', async () => {
  const s = await boot({ config: { colorScheme: 'dark' } });
  await until('kit', () => s.shown('kit-screen'));
  assert.equal(s.state().scheme, 'dark');
  assert.equal(s.win.document.body.style.backgroundColor, 'rgb(14, 17, 22)');
  s.send('theme', { colorScheme: 'light' });
  await until('light', () => s.state().scheme === 'light');
  assert.equal(s.win.document.body.style.backgroundColor, 'rgb(255, 255, 255)');
  s.send('theme', { colorScheme: 'purple' });
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(s.state().scheme, 'light', 'an unknown scheme is ignored');
  s.close();
});

test('in dark, plain Text and TextInput default to the theme text colour', async () => {
  const s = await boot({ initialPath: '/plain', edits: withStyle('dark') });
  await until('plain', () => s.shown('plain-screen'));
  assert.equal(s.$('scheme').textContent, 'dark', 'useColorScheme answers for the app');
  assert.equal(s.color('plain-text'), 'rgb(230, 237, 243)');
  assert.equal(s.color('red-text'), 'rgb(255, 0, 0)', "the app's own colour wins");
  assert.equal(s.color('plain-input'), 'rgb(230, 237, 243)');
  // Nested Text inherits from its parent rather than getting its own default.
  assert.equal(s.$('nested-text').style.color, '');
  assert.deepEqual(s.errors(), []);
  s.close();
});

test('an app without userInterfaceStyle stays light on a dark host, as do bundles built before it', async () => {
  const { userInterfaceStyle: _, ...noStyle } = appJson;
  const fresh = await boot({ initialPath: '/plain', config: { colorScheme: 'dark' }, edits: { 'app.json': JSON.stringify(noStyle) } });
  await until('plain', () => fresh.shown('plain-screen'));
  const legacy = await boot({ initialPath: '/plain', config: { colorScheme: 'dark' }, legacy: true });
  await until('plain', () => legacy.shown('plain-screen'));
  for (const s of [fresh, legacy]) {
    assert.equal(s.$('scheme').textContent, 'light');
    assert.equal(s.color('plain-text'), 'rgb(0, 0, 0)');
    assert.equal(s.win.document.body.style.backgroundColor, '');
    s.close();
  }
});
