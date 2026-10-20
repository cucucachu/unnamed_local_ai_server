// @homeai/ui and the theme (M22-03) in jsdom, with the kit-check fixture:
// the kit's inputs and rows drive app state, and the theme follows app.json's
// userInterfaceStyle and the host's scheme; bundles built before it stay light.
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

test('an app without userInterfaceStyle follows the host; bundles built before it stay light', async () => {
  const { userInterfaceStyle: _, ...noStyle } = appJson;
  const fresh = await boot({ initialPath: '/plain', config: { colorScheme: 'dark' }, edits: { 'app.json': JSON.stringify(noStyle) } });
  await until('plain', () => fresh.shown('plain-screen'));
  assert.equal(fresh.$('scheme').textContent, 'dark');
  assert.equal(fresh.color('plain-text'), 'rgb(230, 237, 243)');
  fresh.close();
  const legacy = await boot({ initialPath: '/plain', config: { colorScheme: 'dark' }, legacy: true });
  await until('plain', () => legacy.shown('plain-screen'));
  assert.equal(legacy.$('scheme').textContent, 'light');
  assert.equal(legacy.color('plain-text'), 'rgb(0, 0, 0)');
  assert.equal(legacy.win.document.body.style.backgroundColor, '');
  assert.equal(legacy.win.document.documentElement.style.backgroundColor, 'rgb(255, 255, 255)', "repaints the document's dark start");
  legacy.close();
});

// --- charts (M22-04) ---------------------------------------------------------

test('BarChart: two series, negative values, empty', async () => {
  const s = await boot({ initialPath: '/charts' });
  await until('charts', () => s.shown('charts-screen'));
  const two = s.$('bar-two');
  assert.equal(two.getAttribute('role'), 'img');
  assert.equal(
    two.getAttribute('aria-label'),
    'Spending: bar chart of 3 points. Spent: from $420.00 (Jan) to $380.00 (Mar), highest $515.00 (Feb), lowest $380.00 (Mar). Budget: from $500.00 (Jan) to $450.00 (Mar), highest $500.00 (Jan), lowest $450.00 (Mar).',
  );
  assert.equal(two.querySelectorAll('rect[data-bar]').length, 6);
  assert.deepEqual([...two.querySelectorAll('text[data-axis="x"]')].map((t) => t.textContent), ['Jan', 'Feb', 'Mar']);
  assert.match(two.textContent, /Spent.*Budget/, 'a legend for two series');
  assert.equal(two.querySelector('svg').getAttribute('aria-hidden'), 'true');

  // Bars grow up or down from the zero line.
  const neg = s.$('bar-negative');
  const ticks = [...neg.querySelectorAll('text[data-axis="y"]')].map((t) => t.textContent);
  assert.ok(ticks.includes('0') && ticks.some((v) => v.startsWith('-')), ticks.join(' '));
  const zeroY = Number([...neg.querySelectorAll('line')].find((l) => l.getAttribute('stroke-width') === '1.5').getAttribute('y1'));
  const [w1, w2] = ['net:0', 'net:1'].map((k) => neg.querySelector(`rect[data-bar="${k}"]`));
  assert.ok(Math.abs(Number(w1.getAttribute('y')) + Number(w1.getAttribute('height')) - zeroY) < 0.01, 'positive bar ends at 0');
  assert.ok(Math.abs(Number(w2.getAttribute('y')) - zeroY) < 0.01, 'negative bar starts at 0');
  assert.equal(neg.textContent.includes('net'), false, 'no legend for one series');

  assert.equal(s.$('bar-empty-empty').textContent, 'No data yet');
  assert.equal(s.$('bar-empty').getAttribute('aria-label'), 'Bar chart: no data');
  assert.equal(s.$('bar-empty').querySelector('svg'), null);
  assert.deepEqual(s.errors(), []);
  s.close();
});

test('LineChart: many points thin their labels, one point, a gap, empty', async () => {
  const s = await boot({ initialPath: '/charts' });
  await until('charts', () => s.shown('charts-screen'));
  const many = s.$('line-many');
  const labels = [...many.querySelectorAll('text[data-axis="x"]')].map((t) => t.textContent);
  assert.ok(labels.length >= 3 && labels.length <= 8, `x labels fit a phone width: ${labels.join(' ')}`);
  assert.equal(labels[0], '08-01');
  assert.equal(many.querySelectorAll('circle').length, 0, 'no dots past 31 points');
  assert.equal(many.querySelector('path[data-line="kg"]').getAttribute('d').match(/[ML]/g).length, 60);
  // A line fits its range instead of starting at 0.
  const ticks = [...many.querySelectorAll('text[data-axis="y"]')].map((t) => Number(t.textContent));
  assert.ok(Math.min(...ticks) >= 70, ticks.join(' '));
  assert.match(many.getAttribute('aria-label'), /^Weight: line chart of 60 points\. kg: from 82 \(08-01\) to /);

  const one = s.$('line-one');
  assert.equal(one.querySelectorAll('circle').length, 1);
  assert.equal(one.getAttribute('aria-label'), 'Line chart: line chart of 1 point. kg: 81.4 (Today).');

  const gap = s.$('line-gap').querySelector('path[data-line="km"]').getAttribute('d');
  assert.equal(gap.match(/M/g).length, 2, `a missing value breaks the line: ${gap}`);
  assert.equal(s.$('line-gap').querySelectorAll('circle').length, 2);

  assert.equal(s.$('line-empty-empty').textContent, 'No data yet');
  s.close();
});

test('PieChart: slices, legend with percents, one slice, empty', async () => {
  const s = await boot({ initialPath: '/charts' });
  await until('charts', () => s.shown('charts-screen'));
  const pie = s.$('pie');
  assert.equal(pie.getAttribute('aria-label'), 'Categories: Food 23% (300), Rent 69% (900), Fun 8% (100).');
  assert.deepEqual([...pie.querySelectorAll('[data-slice]')].map((p) => p.getAttribute('data-slice')), ['Food', 'Rent', 'Fun'], 'non-positive values are left out');
  assert.match(pie.textContent, /Food23%Rent69%Fun8%/);
  assert.equal(s.$('pie-one').querySelectorAll('circle[data-slice]').length, 1, 'one slice is a full ring');
  assert.equal(s.$('pie-empty-empty').textContent, 'No data yet');
  assert.deepEqual(s.errors(), []);
  s.close();
});

test('charts follow the theme', async () => {
  const s = await boot({ initialPath: '/charts', edits: withStyle('dark') });
  await until('charts', () => s.shown('charts-screen'));
  assert.equal(s.$('bar-negative').querySelector('text[data-axis="y"]').getAttribute('fill'), '#8b949e');
  assert.equal(s.$('bar-negative').querySelector('rect[data-bar]').getAttribute('fill'), '#4f8cff');
  s.close();
});
