// Builder tests against fixture apps: a valid app builds and renders, and
// each kind of mistake produces one precise diagnostic.
//   npm test    (builds dist/runtime*.js first)
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { compilePhase, smokePhase } from '../src/cli.mjs';
import { ALLOWED_MODULES, BANNER } from '../src/compile.mjs';
import { MAX_DIAGNOSTICS, writeResult } from '../src/diagnostics.mjs';
import { RUNTIME_DEV } from '../src/smoke.mjs';
import { SDK_TYPES } from '../src/sdk.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const GROCERIES = path.join(here, 'fixtures', 'groceries');

/** A copy of the groceries fixture with `edits` applied: {file: content | (old) => new | null}. */
function app(edits = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'homeai-builder-'));
  fs.cpSync(GROCERIES, dir, { recursive: true });
  for (const [rel, edit] of Object.entries(edits)) {
    const file = path.join(dir, rel);
    if (edit === null) fs.rmSync(file);
    else {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      const old = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '';
      fs.writeFileSync(file, typeof edit === 'function' ? edit(old) : edit);
    }
  }
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'homeai-builder-out-'));
  return { dir, out };
}

const prepend = (line) => (old) => `${line}\n${old}`;
const pick = (d) => ({ step: d.step, file: d.file, line: d.line, column: d.column });

async function compileOnly(edits) {
  const { dir, out } = app(edits);
  return { ...(await compilePhase(dir, out)), dir, out };
}

async function build(edits) {
  const compiled = await compileOnly(edits);
  assert.equal(compiled.ok, true, JSON.stringify(compiled.diagnostics));
  writeResult(compiled.out, compiled);
  return smokePhase(compiled.dir, compiled.out);
}

test('a valid app compiles, type-checks and renders every route', async () => {
  const compiled = await compileOnly();
  assert.deepEqual(compiled.diagnostics, []);
  assert.equal(compiled.ok, true);
  assert.deepEqual(
    compiled.routes.map((r) => r.name),
    ['index', 'item/[id]'],
  );
  for (const f of ['app.js', 'app.js.map', 'app.dev.js', 'app.dev.js.map']) assert.ok(fs.existsSync(path.join(compiled.out, f)), f);
  const code = fs.readFileSync(path.join(compiled.out, 'app.js'), 'utf8');
  assert.ok(code.startsWith(BANNER));
  const required = new Set([...code.matchAll(/require\("([^"]+)"\)/g)].map((m) => m[1]));
  assert.deepEqual([...required].filter((m) => !ALLOWED_MODULES.includes(m)), []);
  assert.ok(compiled.stats.bytes > 0 && compiled.stats.typecheck_ms > 0);

  writeResult(compiled.out, compiled);
  const smoke = await smokePhase(compiled.dir, compiled.out);
  assert.deepEqual(smoke.diagnostics, []);
  assert.deepEqual(
    smoke.routes.map((r) => [r.path, r.timed_out]),
    [
      ['/', false],
      ['/item/1', false],
    ],
  );
});

test('a screen querying a merged view renders against the reads.sql stand-in', async () => {
  const screen = [
    "import { Text } from 'react-native';",
    "import { useQuery } from '@homeai/sdk';",
    'export default function Events() {',
    "  const { data } = useQuery<{ title: string; _space: string }>('SELECT title, _space FROM calendar_events', []);",
    '  return <Text>{(data ?? []).length}</Text>;',
    '}',
    '',
  ].join('\n');
  const compiled = await compileOnly({ 'app/events.tsx': screen });
  assert.equal(compiled.ok, true, JSON.stringify(compiled.diagnostics));
  writeResult(compiled.out, compiled);

  const missing = await smokePhase(compiled.dir, compiled.out, compiled.out);
  assert.equal(missing.ok, false);
  assert.match(missing.diagnostics[0].message, /no such table: calendar_events/);

  fs.writeFileSync(path.join(compiled.out, 'reads.sql'), 'CREATE TABLE "calendar_events" ("id", "title", "_space");\n');
  const smoke = await smokePhase(compiled.dir, compiled.out, compiled.out);
  assert.deepEqual(smoke.diagnostics, []);

  fs.writeFileSync(path.join(compiled.out, 'reads.sql'), 'CREATE TABLE "items" ("x");\n');
  const clash = await smokePhase(compiled.dir, compiled.out, compiled.out);
  assert.match(clash.diagnostics[0].message, /stand-ins for homeai\.reads/);
});

// The reference app (M12-07) and the M13-03 templates. Not in the image,
// which copies only tests/.
const TEMPLATES = path.resolve(here, '../../../examples/apps');
const TEMPLATE_ROUTES = {
  'grocery-list': { names: ['index', 'item/[id]'], paths: ['/', '/item/1'] },
  list: { names: ['index', 'item/[id]'], paths: ['/', '/item/1'] },
  notes: { names: ['index', 'note/[id]'], paths: ['/', '/note/1'] },
  tracker: { names: ['habit/[id]', 'index'], paths: ['/habit/1', '/'] },
};

for (const [slug, expect] of Object.entries(TEMPLATE_ROUTES)) {
  const dir = path.join(TEMPLATES, slug);
  test(
    `template ${slug} builds with no diagnostics`,
    { skip: !fs.existsSync(dir) && 'examples/ is not here' },
    async () => {
      const out = fs.mkdtempSync(path.join(os.tmpdir(), 'homeai-builder-out-'));
      const compiled = await compilePhase(dir, out);
      assert.deepEqual(compiled.diagnostics, []);
      assert.deepEqual(
        compiled.routes.map((r) => r.name),
        expect.names,
      );
      writeResult(out, compiled);
      const smoke = await smokePhase(dir, out);
      assert.deepEqual(smoke.diagnostics, []);
      assert.deepEqual(
        smoke.routes.map((r) => [r.path, r.timed_out]),
        expect.paths.map((p) => [p, false]),
      );
    },
  );
}

test('a forbidden import is an import diagnostic at the import, even if unused', async () => {
  const r = await compileOnly({ 'app/index.tsx': prepend("import { createPortal } from 'react-dom';") });
  assert.equal(r.ok, false);
  assert.deepEqual(r.diagnostics.map(pick), [{ step: 'import', file: 'app/index.tsx', line: 1, column: 30 }]);
  assert.match(r.diagnostics[0].message, /Import "react-dom" is not allowed in apps\. Allowed modules: react, react-native, expo-router, expo-sqlite, @homeai\/sdk/);
  assert.equal(r.diagnostics[0].source, "import { createPortal } from 'react-dom';");
  assert.ok(!fs.existsSync(path.join(r.out, 'app.js')));
});

test('require() of a Node module and imports in helper files are caught too', async () => {
  const r = await compileOnly({
    'lib/helper.ts': "import os from 'node:os';\nexport const n = os.cpus().length;\n",
    'app/index.tsx': (old) => old.replace("import { useState } from 'react';", "import { useState } from 'react';\nimport { n } from '../lib/helper';\nconst fs = require('fs');"),
  });
  assert.deepEqual(r.diagnostics.map(pick), [
    { step: 'import', file: 'app/index.tsx', line: 3, column: 20 },
    { step: 'import', file: 'lib/helper.ts', line: 1, column: 16 },
  ]);
});

test('a relative import outside the app folder is refused', async () => {
  const r = await compileOnly({ 'app/index.tsx': prepend("import secret from '../../etc/passwd';") });
  assert.deepEqual(r.diagnostics.map(pick), [{ step: 'import', file: 'app/index.tsx', line: 1, column: 20 }]);
  assert.match(r.diagnostics[0].message, /points outside the app folder/);
});

test('a symlink that leaves the app folder is refused', async () => {
  const { dir, out } = app({ 'app/index.tsx': prepend("import { x } from '../lib/linked';") });
  fs.mkdirSync(path.join(dir, 'lib'));
  fs.symlinkSync('/etc/hostname', path.join(dir, 'lib', 'linked.ts'));
  const r = await compilePhase(dir, out);
  assert.equal(r.ok, false);
  assert.equal(r.diagnostics[0].step, 'import');
  assert.match(r.diagnostics[0].message, /lib\/linked\.ts is a link outside the app folder/);
});

test('a broken relative import names the file and position', async () => {
  const r = await compileOnly({ 'app/index.tsx': prepend("import { thing } from '../components/Thing';") });
  assert.deepEqual(r.diagnostics.map(pick), [{ step: 'import', file: 'app/index.tsx', line: 1, column: 23 }]);
  assert.match(r.diagnostics[0].message, /Could not resolve "\.\.\/components\/Thing"/);
});

test('a type error is a type diagnostic and no bundle is written', async () => {
  const r = await compileOnly({
    'app/item/[id].tsx': (old) => old.replace('const item = data?.[0];', 'const item = data?.[0];\n  const n: number = item?.name;'),
  });
  assert.equal(r.ok, false);
  assert.deepEqual(r.diagnostics.map(pick), [{ step: 'type', file: 'app/item/[id].tsx', line: 13, column: 9 }]);
  assert.match(r.diagnostics[0].message, /^TS2322: Type 'string \| undefined' is not assignable to type 'number'/);
  assert.equal(r.diagnostics[0].source, 'const n: number = item?.name;');
  assert.ok(!fs.existsSync(path.join(r.out, 'app.js')));
});

test('network and DOM globals are refused with an explanation, not a lib hint', async () => {
  const r = await compileOnly({
    'app/index.tsx': (old) => old.replace('async function add() {', "async function add() {\n    await fetch('https://example.com');\n    console.log(document.title, new XMLHttpRequest());"),
  });
  assert.deepEqual(
    r.diagnostics.map((d) => [d.line, d.column, d.message.split(':')[0]]),
    [
      [14, 11, '"fetch" is not available in apps'],
      [15, 17, '"document" is not available in apps'],
      [15, 37, '"XMLHttpRequest" is not available in apps'],
    ],
  );
  assert.ok(r.diagnostics.every((d) => !/lib' compiler option/.test(d.message)));
});

test('a local named like a forbidden global is fine', async () => {
  const r = await compileOnly({
    'app/index.tsx': (old) => old.replace('async function add() {', 'async function add() {\n    const fetch = (n: string) => n;\n    fetch(name);'),
  });
  assert.deepEqual(r.diagnostics, []);
});

const inAdd = (...lines) => ({
  'app/index.tsx': (old) => old.replace('async function add() {', ['async function add() {', ...lines.map((l) => `    ${l}`)].join('\n')),
});
const found = (r) => r.diagnostics.map((d) => [d.line, d.column, d.message.split(':')[0]]);

test('forbidden globals reached through globalThis are refused', async () => {
  const r = await compileOnly(
    inAdd(
      "await globalThis.fetch('https://example.com');",
      "new globalThis['XMLHttpRequest']();",
      'globalThis.globalThis.require;',
      '(globalThis as any).WebSocket;',
      'const key = String(name);',
      'console.log(globalThis[key]);',
    ),
  );
  assert.deepEqual(found(r), [
    [14, 22, '"fetch" is not available in apps'],
    [15, 20, '"XMLHttpRequest" is not available in apps'],
    [16, 27, '"require" is not available in apps'],
    [17, 25, '"WebSocket" is not available in apps'],
    [19, 28, 'computed access on globalThis is not allowed in apps; use the global directly'],
  ]);
});

test('globalThis cannot be aliased or destructured to get around the check', async () => {
  const r = await compileOnly(
    inAdd('const g = globalThis;', "await g.fetch('https://example.com');", 'const { WebSocket: W } = globalThis;', 'console.log(W);', "Reflect.get(globalThis.globalThis, 'fetch');"),
  );
  const asValue = 'globalThis can only be used as globalThis.<name> in apps, not as a value; use the global directly';
  assert.deepEqual(found(r), [
    [14, 15, asValue],
    [15, 13, '"fetch" is not available in apps'],
    [16, 13, '"WebSocket" is not available in apps'],
    [16, 30, asValue],
    [18, 17, asValue],
  ]);
});

test('self and global are refused like window', async () => {
  const r = await compileOnly(inAdd('console.log(self, global);'));
  assert.deepEqual(found(r), [
    [14, 17, '"self" is not available in apps'],
    [14, 23, '"global" is not available in apps'],
  ]);
});

test('allowed globals through globalThis, and a local named globalThis, are fine', async () => {
  const r = await compileOnly(
    inAdd("globalThis.setTimeout(() => {}, 0);", "console.log(globalThis['Math'].max(1, 2), globalThis.Math.PI);", 'const t: typeof globalThis.setTimeout = setTimeout;', 'console.log(t);'),
  );
  assert.deepEqual(r.diagnostics, []);
  const local = await compileOnly(inAdd('const globalThis = { fetch: (n: string) => n };', 'globalThis.fetch(name);', "console.log(globalThis['fetch']);"));
  assert.deepEqual(local.diagnostics, []);
});

test('triple-slash directives are refused (they could bring the DOM lib back)', async () => {
  const r = await compileOnly({ 'app/index.tsx': prepend('/// <reference lib="dom" />') });
  assert.deepEqual(r.diagnostics.map(pick), [{ step: 'type', file: 'app/index.tsx', line: 1, column: 21 }]);
  assert.match(r.diagnostics[0].message, /triple-slash directives/);
});

test('expo-router features the shim lacks are type errors', async () => {
  const r = await compileOnly({ 'app/_layout.tsx': (old) => old.replace('import { Stack }', 'import { Stack, Tabs }') });
  assert.deepEqual(r.diagnostics.map(pick), [{ step: 'type', file: 'app/_layout.tsx', line: 1, column: 17 }]);
  assert.match(r.diagnostics[0].message, /has no exported member 'Tabs'/);
});

test('withTransactionAsync is not in SDK 1', async () => {
  const r = await compileOnly({
    'app/index.tsx': (old) => old.replace('async function add() {', 'async function add() {\n    await db.withTransactionAsync(async () => {});'),
  });
  assert.equal(r.diagnostics.length, 1);
  assert.match(r.diagnostics[0].message, /Property 'withTransactionAsync' does not exist/);
});

test('an unsupported route is a route diagnostic', async () => {
  const r = await compileOnly({ 'app/(tabs)/home.tsx': 'export default function Home() { return null; }\n' });
  assert.deepEqual(r.diagnostics.map(pick), [{ step: 'route', file: 'app/(tabs)/home.tsx', line: null, column: null }]);
});

test('a render-time throw is source-mapped, with the component stack', async () => {
  const smoke = await build({
    'app/item/[id].tsx': (old) => old.replace('const item = data?.[0];', 'const item = data?.[0];\n  const heading = data![0].name.toUpperCase();'),
  });
  assert.equal(smoke.ok, false);
  assert.deepEqual(smoke.diagnostics.map(pick), [{ step: 'render', file: 'app/item/[id].tsx', line: 13, column: 19 }]);
  const [d] = smoke.diagnostics;
  assert.match(d.message, /^Cannot read properties of undefined \(reading '0'\) \(on screen \/item\/1, in ItemScreen \(app\/item\/\[id\]\.tsx:\d+:\d+\)\)$/);
  assert.equal(d.source, 'const heading = data![0].name.toUpperCase();');
});

test('a SQL error names the query and where it is in the source', async () => {
  const smoke = await build({ 'app/index.tsx': (old) => old.replace('SELECT id, name, done', 'SELECT id, nam, done') });
  assert.deepEqual(smoke.diagnostics.map(pick), [{ step: 'sql', file: 'app/index.tsx', line: 10, column: 52 }]);
  assert.equal(smoke.diagnostics[0].message, 'no such column: nam in SQL: SELECT id, nam, done FROM items ORDER BY id');
});

test('a schema.sql that does not run is a sql diagnostic on schema.sql', async () => {
  const smoke = await build({ 'schema.sql': 'CREATE TABLE items (id INTEGER PRIMARY KEY,, name TEXT);' });
  assert.deepEqual(smoke.diagnostics.map(pick), [{ step: 'sql', file: 'schema.sql', line: null, column: null }]);
  assert.match(smoke.diagnostics[0].message, /^schema\.sql doesn't run: near ","/);
});

const insertTitle = (sql) => (old) => old.replace("db.runAsync('INSERT INTO items (name) VALUES (?)'", `db.runAsync('${sql}'`);

test('a bad query in an event handler is a sql diagnostic, though the smoke render never runs it', async () => {
  const smoke = await build({ 'app/index.tsx': insertTitle('INSERT INTO items (title) VALUES (?)') });
  assert.deepEqual(smoke.diagnostics.map(pick), [{ step: 'sql', file: 'app/index.tsx', line: 15, column: 24 }]);
  assert.equal(smoke.diagnostics[0].message, 'table items has no column named title in SQL: INSERT INTO items (title) VALUES (?)');
});

test('a SQL error the lint finds is not reported again by the render', async () => {
  const smoke = await build({ 'app/index.tsx': (old) => old.replace('ORDER BY id', 'ORDER BY idd') });
  assert.equal(smoke.diagnostics.length, 1);
  assert.doesNotMatch(smoke.diagnostics[0].message, /on screen/);
});

const ADD_ITEM = "-- Adds an item.\nINSERT INTO items (name)\nVALUES (trim(:name));\n\nUPDATE items SET done = 0 WHERE name = ';' ;\n";

test('valid actions, and runAction calls that pass their params, build clean', async () => {
  const smoke = await build({
    'actions/addItem.sql': ADD_ITEM,
    'app/index.tsx': (old) => old.replace("await db.runAsync('INSERT INTO items (name) VALUES (?)', [name.trim()]);", "await runAction('addItem', { name });").replace("import { useDatabase, useQuery } from '@homeai/sdk';", "import { runAction, useDatabase, useQuery } from '@homeai/sdk';"),
  });
  assert.deepEqual(smoke.diagnostics, []);
});

test('an action that does not compile is a sql diagnostic at its statement', async () => {
  const smoke = await build({
    'actions/addItem.sql': '-- Adds an item.\nINSERT INTO items (name)\nVALUES (trim(:name))\nWHERE length(trim(:name)) > 0;\n',
    'actions/rename.sql': 'UPDATE items SET name = :name WHERE id = :id;\nUPDATE items SET title = :name WHERE id = :id;\n',
  });
  assert.deepEqual(smoke.diagnostics.map(pick), [
    { step: 'sql', file: 'actions/addItem.sql', line: 2, column: null },
    { step: 'sql', file: 'actions/rename.sql', line: 2, column: null },
  ]);
  assert.match(smoke.diagnostics[0].message, /^near "WHERE": syntax error in action addItem: INSERT INTO items \(name\)\nVALUES/);
  assert.equal(smoke.diagnostics[0].source, 'INSERT INTO items (name)');
  assert.match(smoke.diagnostics[1].message, /^no such column: title in action rename: /);
});

test('runAction needs the action file and its params', async () => {
  const smoke = await build({
    'actions/addItem.sql': ADD_ITEM,
    'app/index.tsx': (old) =>
      old
        .replace("await db.runAsync('INSERT INTO items (name) VALUES (?)', [name.trim()]);", "await runAction('addItem', { title: name });\n    await runAction('additem', {});")
        .replace("import { useDatabase, useQuery } from '@homeai/sdk';", "import { runAction, useDatabase, useQuery } from '@homeai/sdk';"),
  });
  assert.deepEqual(smoke.diagnostics.map(pick), [
    { step: 'sql', file: 'app/index.tsx', line: 15, column: 22 },
    { step: 'sql', file: 'app/index.tsx', line: 16, column: 22 },
  ]);
  assert.equal(smoke.diagnostics[0].message, "runAction('addItem') doesn't pass :name, which actions/addItem.sql uses");
  assert.equal(smoke.diagnostics[1].message, "runAction('additem') has no actions/additem.sql (the app's actions: addItem)");
});

test('every table has the platform attribution columns, and screens can show who wrote a row', async () => {
  const smoke = await build({
    'app/index.tsx': (old) =>
      old
        .replace("import { useDatabase, useQuery } from '@homeai/sdk';", "import { useDatabase, useMember, useQuery, useUser } from '@homeai/sdk';")
        .replace("useQuery<Item>('SELECT id, name, done FROM items ORDER BY id')", "useQuery<Item>('SELECT id, name, done, _created_by, _updated_at FROM items ORDER BY _created_at')")
        .replace("  const [name, setName] = useState('');", "  const [name, setName] = useState('');\n  const me = useUser();\n  const owner = useMember(me?.id);\n  if (!owner || owner.name !== 'Smoke tester') throw new Error('no members');"),
    'schema.sql': (old) => `${old}\nCREATE INDEX items_by ON items (_created_by);\n`,
  });
  assert.deepEqual(smoke.diagnostics, []);
});

test('a schema.sql that declares an attribution column is a sql diagnostic', async () => {
  const smoke = await build({ 'schema.sql': 'CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, _created_by TEXT);' });
  assert.deepEqual(smoke.diagnostics.map(pick), [{ step: 'sql', file: 'schema.sql', line: null, column: null }]);
  assert.match(smoke.diagnostics[0].message, /table items declares _created_by; the platform adds _created_by, _created_at, _updated_by, _updated_at/);
});

test('result.json caps the diagnostics', () => {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'homeai-builder-cap-'));
  const many = Array.from({ length: 80 }, (_, i) => ({ step: 'type', file: 'a.ts', path: '', line: i, column: 1, message: 'x' }));
  writeResult(out, { ok: false, diagnostics: many });
  assert.equal(JSON.parse(fs.readFileSync(path.join(out, 'result.json'), 'utf8')).diagnostics.length, MAX_DIAGNOSTICS);
});

test('the runtime provides exactly the allowed modules', () => {
  const dom = new JSDOM('<!doctype html><div id="root"></div>', { runScripts: 'outside-only', pretendToBeVisual: true });
  dom.window.ReactNativeWebView = { postMessage() {} };
  const ctx = dom.getInternalVMContext();
  new vm.Script(fs.readFileSync(RUNTIME_DEV, 'utf8')).runInContext(ctx);
  let req;
  dom.window.__homeai_define((require) => {
    req = require;
  });
  for (const m of ALLOWED_MODULES) assert.ok(req(m), m);
  for (const m of ['react-dom', 'react-dom/client', 'fs', 'react-native-web']) assert.throws(() => req(m), /not available in the app sandbox/);
  const typings = fs.readFileSync(SDK_TYPES, 'utf8');
  const declared = [...typings.matchAll(/declare module '([^']+)'/g)].map((m) => m[1]);
  assert.deepEqual(declared.sort(), ALLOWED_MODULES.filter((m) => !m.startsWith('react')).sort());
  dom.window.close();
});
