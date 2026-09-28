// Experiment 5: smoke-render every route of an app with no network.
//
// For each route: fresh jsdom window, the real runtime (dev build) and the
// app bundle (dev build) evaluated in it, the bridge wired to an in-memory
// node:sqlite database created from the app's schema.sql. Effects run, so
// useQuery really executes its SQL. Render errors are caught by the runtime's
// ErrorBoundary / window error handlers and source-mapped to file:line:col,
// component stack included; SQL errors and React warnings are reported too.
//
//   node smoke/smoke-render.mjs <appDir>...      (prints JSON, exit 1 on errors)
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath } from 'node:url';
import { JSDOM, VirtualConsole } from 'jsdom';
import { TraceMap, originalPositionFor } from '@jridgewell/trace-mapping';
import { compileApp } from '../build/compile-app.mjs';

const spike = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const RUNTIME = path.join(spike, 'dist/runtime.dev.js');
const APP_FILE = 'app.js';

function samplePath(route) {
  const segs = route.segments.map((s) => (s.startsWith('[...') ? 'a/b' : s.startsWith('[') ? '1' : s));
  return '/' + segs.join('/');
}

function makeMapper(map, appRoot) {
  const tm = new TraceMap(map);
  const sourceLine = (file, line) => {
    try {
      return fs.readFileSync(path.join(appRoot, file), 'utf8').split('\n')[line - 1]?.trim();
    } catch {
      return undefined;
    }
  };
  const mapFrame = (line, column) => {
    const o = originalPositionFor(tm, { line, column: column - 1 });
    if (!o.source || o.source.includes('<routes>')) return null;
    const file = path.relative(appRoot, path.resolve(appRoot, decodeURIComponent(o.source))).split(path.sep).join('/');
    return { file, line: o.line, column: o.column + 1, name: o.name ?? undefined };
  };
  // Map every "app.js:L:C" in a stack / component stack; keep function names.
  const mapStack = (stack = '') =>
    stack
      .split('\n')
      .map((l) => {
        const m = l.match(/at (?:(\S+) \()?.*?app\.js:(\d+):(\d+)\)?/);
        if (!m) return null;
        const f = mapFrame(+m[2], +m[3]);
        return f ? { fn: m[1] ?? null, ...f } : null;
      })
      .filter(Boolean);
  return { mapStack, sourceLine };
}

async function renderRoute({ runtime, app, appRoot, schema, route, settleMs, timeoutMs }) {
  const routePath = samplePath(route);
  const diagnostics = [];
  const warnings = [];
  const db = new DatabaseSync(':memory:');
  db.exec(schema);
  const vc = new VirtualConsole();
  vc.on('error', (...a) => warnings.push(a.map(String).join(' ').slice(0, 400)));
  vc.on('warn', (...a) => warnings.push(a.map(String).join(' ').slice(0, 400)));
  vc.on('jsdomError', (e) => warnings.push(`jsdom: ${e.message}`));
  const dom = new JSDOM('<!doctype html><html><head></head><body><div id="root"></div></body></html>', {
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    virtualConsole: vc,
    url: 'about:blank',
  });
  const win = dom.window;
  // jsdom has no layout; onLayout never fires, but react-native-web wants the constructor.
  win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  let pending = 0;
  let lastActivity = Date.now();
  let ready = false;
  const events = [];
  // Same transport the WebView uses: page -> host via ReactNativeWebView.postMessage.
  win.ReactNativeWebView = {
    postMessage(raw) {
      const env = JSON.parse(raw);
      lastActivity = Date.now();
      if (env.kind === 'evt') {
        events.push(env);
        if (env.event === 'runtime.ready') ready = true;
        return;
      }
      pending++;
      queueMicrotask(() => {
        let res;
        try {
          const st = db.prepare(env.params.sql);
          const args = env.params.params ?? [];
          let result;
          if (env.method === 'db.getAll') result = st.all(...args);
          else if (env.method === 'db.getFirst') result = st.get(...args) ?? null;
          else if (env.method === 'db.run') {
            const r = st.run(...args);
            result = { changes: Number(r.changes), lastInsertRowId: Number(r.lastInsertRowid) };
          } else throw new Error(`method ${env.method} not available in smoke render`);
          res = { homeai: 1, kind: 'res', id: env.id, ok: true, result };
        } catch (err) {
          diagnostics.push({ severity: 'error', kind: 'sql', route: route.name, path: routePath, message: err.message, sql: env.params?.sql });
          res = { homeai: 1, kind: 'res', id: env.id, ok: false, error: { code: 'sql', message: err.message } };
        }
        pending--;
        lastActivity = Date.now();
        win.__homeaiReceive(JSON.stringify(res));
      });
    },
  };
  win.__homeai_config = { initialPath: routePath };
  const ctx = dom.getInternalVMContext();
  new vm.Script(runtime, { filename: 'runtime.js' }).runInContext(ctx);
  new vm.Script(app.code, { filename: APP_FILE }).runInContext(ctx);

  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    await new Promise((r) => setTimeout(r, 10));
    if (ready && pending === 0 && Date.now() - lastActivity >= settleMs) break;
  }
  const { mapStack, sourceLine } = makeMapper(app.map, appRoot);
  for (const e of events.filter((e) => e.event === 'runtime.error')) {
    const frames = mapStack(e.data.stack);
    const components = mapStack(e.data.componentStack);
    const at = frames[0] ?? components[0] ?? null;
    diagnostics.push({
      severity: 'error',
      kind: 'render',
      route: route.name,
      path: routePath,
      message: e.data.message,
      ...(at ? { file: at.file, line: at.line, column: at.column, source: sourceLine(at.file, at.line) } : {}),
      componentStack: components.map((c) => `${c.fn ?? '?'} (${c.file}:${c.line}:${c.column})`),
    });
  }
  const renderedText = win.document.getElementById('root').textContent.slice(0, 200);
  dom.window.close();
  db.close();
  return { route: route.name, path: routePath, ms: Date.now() - start, timedOut: !ready, renderedText, diagnostics, warnings };
}

export async function smokeRender(appDir, { settleMs = 50, timeoutMs = 3000 } = {}) {
  const appRoot = path.resolve(appDir);
  const t0 = performance.now();
  const app = await compileApp(appRoot, { dev: true });
  if (!app.ok) return { app: path.basename(appRoot), ok: false, phase: 'compile', diagnostics: app.diagnostics };
  if (!fs.existsSync(RUNTIME)) throw new Error('dist/runtime.dev.js missing: run `node build/build-runtime.mjs --dev`');
  const runtime = fs.readFileSync(RUNTIME, 'utf8');
  const schema = fs.readFileSync(path.join(appRoot, 'schema.sql'), 'utf8');
  const routes = [];
  for (const route of app.routes) routes.push(await renderRoute({ runtime, app, appRoot, schema, route, settleMs, timeoutMs }));
  const diagnostics = routes.flatMap((r) => r.diagnostics);
  return {
    app: path.basename(appRoot),
    ok: diagnostics.length === 0 && routes.every((r) => !r.timedOut),
    totalMs: +(performance.now() - t0).toFixed(1),
    routes: routes.map(({ diagnostics: _d, ...r }) => r),
    diagnostics,
  };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const reports = [];
  for (const dir of process.argv.slice(2)) reports.push(await smokeRender(dir));
  console.log(JSON.stringify(reports, null, 2));
  process.exitCode = reports.every((r) => r.ok) ? 0 : 1;
}
