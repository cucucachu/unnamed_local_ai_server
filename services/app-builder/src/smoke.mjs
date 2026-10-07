// Smoke render: every route of a compiled app, each in a fresh jsdom window
// with the real runtime (dev build) and the app's dev bundle, the bridge
// wired to an in-memory SQLite database created from schema.sql over the
// same `ReactNativeWebView.postMessage` / `__homeaiReceive` transport the
// phone uses (docs/PLATFORM.md §7 "Build and verify"). Effects run, so
// `useQuery` really executes its SQL.
//
// App code runs here, and jsdom is not a sandbox: code that reaches an
// object from this realm can reach Node. That's why this phase has its own
// container, which can't write to the bundle it renders.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath } from 'node:url';
import { JSDOM, VirtualConsole } from 'jsdom';
import { TraceMap, originalPositionFor } from '@jridgewell/trace-mapping';
import { diagnostic, relative, sourceLine } from './diagnostics.mjs';
import { applySchema, checkSql } from './sqlcheck.mjs';
import { samplePath } from './routes.mjs';

const builderRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const RUNTIME_DEV = path.join(builderRoot, 'dist', 'runtime.dev.js');
const APP_FILE = 'app.js';
const SMOKE_SPACE = { id: '00000000-0000-4000-8000-000000000000', slug: 'smoke-test', name: 'Smoke test', role: 'owner' };
const SMOKE_USER = { id: '00000000-0000-4000-8000-000000000001', username: 'smoke', name: 'Smoke tester' };
const SMOKE_MEMBERS = [{ ...SMOKE_USER, role: 'owner' }];

function mapper(map, appRoot) {
  const tm = new TraceMap(map);
  const frame = (line, column) => {
    const o = originalPositionFor(tm, { line, column: column - 1 });
    if (!o.source || o.source.includes('<routes>')) return null;
    return { file: relative(appRoot, decodeURIComponent(o.source)), line: o.line, column: o.column + 1 };
  };
  return (stack = '') =>
    stack
      .split('\n')
      .map((l) => {
        const m = l.match(/at (?:(\S+) \()?.*?app\.js:(\d+):(\d+)\)?/);
        if (!m) return null;
        const f = frame(+m[2], +m[3]);
        return f ? { fn: m[1] ?? null, ...f } : null;
      })
      .filter(Boolean);
}

function appSources(appRoot) {
  const out = [];
  const walk = (dir) => {
    for (const d of fs.readdirSync(dir, { withFileTypes: true })) {
      if (d.name.startsWith('.') || d.name === 'node_modules') continue;
      const p = path.join(dir, d.name);
      if (d.isDirectory()) walk(p);
      else if (/\.(tsx?|jsx?)$/.test(d.name)) out.push(p);
    }
  };
  walk(appRoot);
  return out;
}

/** Where the SQL text first appears in the app's source, when the stack can't say. */
function findSql(appRoot, sql) {
  const needle = sql.trim();
  if (needle.length < 6) return null;
  for (const abs of appSources(appRoot)) {
    const text = fs.readFileSync(abs, 'utf8');
    const i = text.indexOf(needle);
    if (i < 0) continue;
    const before = text.slice(0, i).split('\n');
    return { file: relative(appRoot, abs), line: before.length, column: before[before.length - 1].length + 1 };
  }
  return null;
}

async function renderRoute({ runtime, app, appRoot, schema, readsSql, reported, route, settleMs, timeoutMs }) {
  const routePath = samplePath(route);
  const diagnostics = [];
  const mapStack = mapper(app.map, appRoot);
  const db = new DatabaseSync(':memory:');
  applySchema(db, schema);
  if (readsSql) db.exec(readsSql);
  const vc = new VirtualConsole();
  const dom = new JSDOM('<!doctype html><html><head></head><body><div id="root"></div></body></html>', {
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    virtualConsole: vc,
    url: 'about:blank',
  });
  const win = dom.window;
  // jsdom has no layout, so onLayout never fires, but react-native-web wants the constructor.
  win.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  let pending = 0;
  let lastActivity = Date.now();
  let ready = false;
  const errors = [];
  const reply = (env) => win.__homeaiReceive(JSON.stringify(env));
  win.ReactNativeWebView = {
    postMessage(raw) {
      const env = JSON.parse(raw);
      lastActivity = Date.now();
      if (env.kind === 'evt') {
        if (env.event === 'runtime.ready') ready = true;
        if (env.event === 'runtime.error') errors.push(env.data);
        return;
      }
      const caller = mapStack(new Error().stack)[0] ?? null;
      pending++;
      queueMicrotask(() => {
        try {
          let result;
          if (env.method === 'action') result = { changes: 0, lastInsertRowId: 0, rows: [] };
          else {
            const st = db.prepare(env.params.sql);
            const p = env.params.params ?? [];
            const args = Array.isArray(p) ? p : [p];
            if (env.method === 'db.getAll') result = st.all(...args);
            else if (env.method === 'db.getFirst') result = st.get(...args) ?? null;
            else if (env.method === 'db.run') {
              const r = st.run(...args);
              result = { changes: Number(r.changes), lastInsertRowId: Number(r.lastInsertRowid) };
            } else throw new Error(`unknown method ${env.method}`);
          }
          reply({ homeai: 1, kind: 'res', id: env.id, ok: true, result });
        } catch (err) {
          const sql = String(env.params?.sql ?? '');
          const at = caller ?? findSql(appRoot, sql);
          if (!reported.has(sql.trim())) diagnostics.push(
            diagnostic({
              step: 'sql',
              ...(at ?? {}),
              source: at && sourceLine(appRoot, at.file, at.line),
              message: `${err.message} in SQL: ${sql} (on screen ${routePath})`,
            }),
          );
          reply({ homeai: 1, kind: 'res', id: env.id, ok: false, error: { code: 'sql', message: err.message } });
        } finally {
          pending--;
          lastActivity = Date.now();
        }
      });
    },
  };
  win.__homeai_config = { initialPath: routePath, user: SMOKE_USER, members: SMOKE_MEMBERS };
  const ctx = dom.getInternalVMContext();
  const start = Date.now();
  try {
    new vm.Script(runtime, { filename: 'runtime.js' }).runInContext(ctx);
    reply({ homeai: 1, kind: 'evt', event: 'space', data: SMOKE_SPACE });
    new vm.Script(app.code, { filename: APP_FILE }).runInContext(ctx);
    while (Date.now() - start < timeoutMs) {
      await new Promise((r) => setTimeout(r, 10));
      if (ready && pending === 0 && Date.now() - lastActivity >= settleMs) break;
    }
  } catch (err) {
    errors.push({ message: err?.message ?? String(err), stack: err?.stack ?? '', componentStack: '' });
  }
  for (const e of errors) {
    const frames = mapStack(e.stack);
    const components = mapStack(e.componentStack);
    const at = frames[0] ?? components[0] ?? null;
    const inside = components.map((c) => `${c.fn ?? '?'} (${c.file}:${c.line}:${c.column})`).join(' < ');
    diagnostics.push(
      diagnostic({
        step: 'render',
        ...(at ? { file: at.file, line: at.line, column: at.column } : {}),
        source: at && sourceLine(appRoot, at.file, at.line),
        message: `${e.message} (on screen ${routePath}${inside ? `, in ${inside}` : ''})`,
      }),
    );
  }
  const timedOut = !ready && !errors.length;
  if (timedOut) {
    diagnostics.push(
      diagnostic({
        step: 'render',
        file: `app/${route.file ?? `${route.name}.tsx`}`,
        message: `screen ${routePath} didn't finish rendering within ${timeoutMs / 1000} s (a render loop, or an effect that never settles?)`,
      }),
    );
  }
  dom.window.close();
  db.close();
  return { path: routePath, ms: Date.now() - start, timed_out: timedOut, diagnostics };
}

/** { ok, diagnostics, routes: [{path, ms, timed_out}] } for a compiled app (`app` = the dev bundle + map).
 * `readsSql` creates empty stand-ins for the merged views the app reads (`<app>_<export>`). */
export async function smokeRender(appRoot, app, routes, { settleMs = 50, timeoutMs = 3000, readsSql = '' } = {}) {
  appRoot = path.resolve(appRoot);
  const runtime = fs.readFileSync(RUNTIME_DEV, 'utf8');
  let schema = '';
  try {
    schema = fs.readFileSync(path.join(appRoot, 'schema.sql'), 'utf8');
    applySchema(new DatabaseSync(':memory:'), schema);
  } catch (err) {
    if (err.code !== 'ENOENT') {
      return { ok: false, diagnostics: [diagnostic({ step: 'sql', file: 'schema.sql', message: `schema.sql doesn't run: ${err.message}` })], routes: [] };
    }
  }
  if (readsSql) {
    try {
      const probe = new DatabaseSync(':memory:');
      applySchema(probe, schema);
      probe.exec(readsSql);
      probe.close();
    } catch (err) {
      return { ok: false, diagnostics: [diagnostic({ step: 'sql', file: 'app.json', message: `the stand-ins for homeai.reads don't fit schema.sql: ${err.message}` })], routes: [] };
    }
  }
  const lintDb = new DatabaseSync(':memory:');
  applySchema(lintDb, schema);
  if (readsSql) lintDb.exec(readsSql);
  const lint = checkSql(appRoot, lintDb);
  lintDb.close();
  const results = [];
  for (const route of routes) results.push(await renderRoute({ runtime, app, appRoot, schema, readsSql, reported: lint.bad, route, settleMs, timeoutMs }));
  const diagnostics = [...lint.diagnostics, ...results.flatMap((r) => r.diagnostics)];
  return { ok: diagnostics.length === 0, diagnostics, routes: results.map(({ diagnostics: _d, ...r }) => r) };
}
