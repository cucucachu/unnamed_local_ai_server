// The router (M22-02) in jsdom: groups, nested layouts, Tabs with a stack per
// tab, modals and header buttons, with the nav-check fixture; and bundles in
// the pre-M22-02 shape (`layout` only).
//   npm test    (builds dist/ first)
import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import { FIXTURE, bootJsdom, until } from './helpers.mjs';

const NAV = path.join(path.dirname(FIXTURE), 'nav-check');
const boot = (opts = {}) => bootJsdom({ appDir: NAV, ...opts });

test('a Stack over a (tabs) group opens on the first tab, with the tab bar', async () => {
  const s = await boot();
  await until('list', () => s.shown('list-screen'));
  assert.deepEqual(s.headings(), ['List']);
  const tabs = [...s.win.document.querySelectorAll('[role="tab"]')];
  assert.deepEqual(tabs.map((t) => t.getAttribute('aria-label')), ['List', 'Settings']);
  assert.deepEqual(tabs.map((t) => t.getAttribute('aria-selected')), ['true', 'false']);
  assert.match(tabs[1].textContent, /3/, 'the badge');
  // Each tab's Ionicons glyph, from the subset font.
  assert.ok(tabs[0].textContent.codePointAt(0) >= 0xe000, 'a private-use glyph');
  assert.ok(s.win.document.head.textContent.includes('@font-face{font-family:Ionicons'));
  assert.equal(s.$('header-back'), null);
  assert.deepEqual(s.errors(), []);
  s.close();
});

test('each tab keeps its own stack; tapping the active tab pops it', async () => {
  const s = await boot();
  s.click('tab-settings');
  await until('settings', () => s.shown('settings-screen'));
  assert.deepEqual(s.headings(), ['Settings']);
  s.click('open-advanced');
  await until('advanced', () => s.shown('advanced-screen'));
  assert.deepEqual(s.headings(), ['Advanced']);
  assert.ok(s.$('header-back'), 'the nested stack has a back button');
  s.click('tab-index');
  await until('list again', () => s.shown('list-screen') && !s.shown('advanced-screen'));
  s.click('tab-settings');
  await until('advanced kept', () => s.shown('advanced-screen'));
  s.click('tab-settings');
  await until('popped to the tab root', () => s.shown('settings-screen') && !s.shown('advanced-screen'));
  // Groups aren't part of the path.
  assert.deepEqual(s.paths(), ['/', '/settings', '/settings/advanced', '/', '/settings/advanced', '/settings']);
  s.close();
});

test('back inside a tab goes back within it', async () => {
  const s = await boot({ initialPath: '/settings' });
  await until('settings', () => s.shown('settings-screen'));
  assert.equal(s.$('header-back'), null);
  s.click('open-advanced');
  await until('advanced', () => s.shown('advanced-screen'));
  s.click('header-back');
  await until('back', () => s.shown('settings-screen'));
  assert.equal(s.$('tab-settings').getAttribute('aria-selected'), 'true');
  s.close();
});

test('a root Stack screen covers the tabs, with its title and a back button', async () => {
  const s = await boot();
  await until('list', () => s.shown('list-screen'));
  s.click('open-item');
  await until('item', () => s.shown('item-screen'));
  assert.equal(s.$('item-screen').textContent, 'Item 7');
  assert.deepEqual(s.headings(), ['Item']);
  assert.equal(s.$('tab-index'), null, 'the tab bar is under the pushed screen');
  s.click('header-back');
  await until('list', () => s.shown('list-screen') && s.shown('tab-index'));
  s.close();
});

test('a modal shows over the screen beneath, with Close and its headerRight', async () => {
  const s = await boot();
  await until('list', () => s.shown('list-screen'));
  s.click('open-new');
  await until('modal', () => s.shown('new-screen'));
  assert.ok(s.$('modal').contains(s.$('new-screen')));
  assert.ok(s.shown('list-screen'), 'the screen beneath stays visible');
  assert.ok(s.$('save'), 'headerRight');
  assert.equal(s.$('save').textContent, 'Save');
  assert.equal(s.$('header-back'), null);
  s.click('header-close');
  await until('closed', () => !s.shown('new-screen') && s.shown('list-screen'));
  s.close();
});

test('a deep link into a tab opens that tab', async () => {
  const s = await boot({ initialPath: '/settings/advanced' });
  await until('advanced', () => s.shown('advanced-screen'));
  assert.equal(s.$('tab-settings').getAttribute('aria-selected'), 'true');
  s.close();
});

test('a bundle in the pre-M22-02 shape (layout only) still runs', async () => {
  const s = await boot({ appDir: FIXTURE, legacy: true });
  await until('index', () => s.$('count'));
  assert.deepEqual(s.headings(), ['Runtime check']);
  assert.deepEqual(s.errors(), []);
  s.close();
});
