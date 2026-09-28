// Shared by the SDK tests and the runtime harness: bundle a fixture app the
// way the builder does (route table + `__homeai_define` CJS body, allowed
// modules external), load the TS host library, and a stand-in platform that
// speaks the real RPC / events contract (docs/ARCHITECTURE.md §3 "App data")
// over node:sqlite.
import * as esbuild from 'esbuild';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { ALLOWED_MODULES, SDK_ROOT } from '../build.mjs';

export const FIXTURE = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures', 'runtime-check');
export const RUNTIME = path.join(SDK_ROOT, 'dist', 'runtime.js');
export const RUNTIME_DEV = path.join(SDK_ROOT, 'dist', 'runtime.dev.js');
export const INSTANCE_ID = '11111111-1111-4111-8111-111111111111';
export const APP_ID = '22222222-2222-4222-8222-222222222222';

function routeFiles(dir, prefix = '') {
  return fs.readdirSync(path.join(dir, prefix), { withFileTypes: true }).flatMap((d) => {
    const rel = path.posix.join(prefix, d.name);
    return d.isDirectory() ? routeFiles(dir, rel) : [rel];
  });
}

/** The app bundle for `appDir`; `edits` = {relative path: new content}. */
export async function bundleApp(appDir = FIXTURE, { dev = true, edits = {} } = {}) {
  const files = routeFiles(path.join(appDir, 'app')).filter((f) => f !== '_layout.tsx');
  const lines = fs.existsSync(path.join(appDir, 'app/_layout.tsx')) ? ["import Layout from './app/_layout.tsx';"] : [];
  const routes = files.map((f, i) => {
    lines.push(`import R${i} from './app/${f}';`);
    const parts = f.replace(/\.tsx?$/, '').split('/');
    return `{ name: ${JSON.stringify(parts.join('/'))}, segments: ${JSON.stringify(parts.filter((p, j) => !(p === 'index' && j === parts.length - 1)))}, component: R${i} }`;
  });
  lines.push(`export const layout = ${lines.length > files.length ? 'Layout' : 'undefined'};`, `export const routes = [${routes.join(', ')}];`);
  const res = await esbuild.build({
    stdin: { contents: lines.join('\n'), resolveDir: appDir, loader: 'js' },
    bundle: true,
    write: false,
    format: 'cjs',
    platform: 'browser',
    target: 'es2020',
    jsx: 'automatic',
    minify: !dev,
    external: ALLOWED_MODULES,
    banner: { js: '__homeai_define(function (require, module, exports) {' },
    footer: { js: '});' },
    logLevel: 'silent',
    plugins: [
      {
        name: 'edits',
        setup(build) {
          build.onLoad({ filter: /\.tsx?$/ }, (args) => {
            const rel = path.relative(appDir, args.path);
            if (!(rel in edits)) return;
            return { contents: edits[rel], loader: 'tsx' };
          });
        },
      },
    ],
  });
  return res.outputFiles[0].text;
}

let hostModule;
/** `@homeai/sdk/host` (and `/host/web` as `.web`), compiled from TypeScript. */
export async function importHost() {
  if (hostModule) return hostModule;
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'homeai-sdk-host-'));
  await esbuild.build({
    entryPoints: { index: path.join(SDK_ROOT, 'src/host/index.ts'), web: path.join(SDK_ROOT, 'src/host/web.ts') },
    outdir: out,
    bundle: true,
    format: 'esm',
    platform: 'neutral',
    logLevel: 'silent',
  });
  hostModule = await import(pathToFileURL(path.join(out, 'index.js')).href);
  return hostModule;
}

/** The host library as one browser IIFE setting `window.HomeaiHost` (for test host pages). */
export async function hostIife() {
  const res = await esbuild.build({
    stdin: {
      contents: "export * from './src/host/index.ts'; export { mountSandboxFrame } from './src/host/web.ts';",
      resolveDir: SDK_ROOT,
      loader: 'ts',
    },
    bundle: true,
    write: false,
    format: 'iife',
    globalName: 'HomeaiHost',
    platform: 'browser',
    target: 'es2020',
    logLevel: 'silent',
  });
  return res.outputFiles[0].text;
}

const split = (sql) => sql.split(/;\s*(?:\n|$)/).filter((s) => s.trim());
const bind = (params) => (params === undefined ? [] : Array.isArray(params) ? params : [params]);
const isRead = (sql) => /^\s*(select|with|values)\b/i.test(sql);
const isWrite = (sql) => /^\s*(insert|update|delete|replace)\b/i.test(sql);

/**
 * A stand-in for the platform's per-instance RPC: `rpc(body)` answers exactly
 * like `POST …/instances/{id}/rpc` ({status, body}); `onEvent(fn)` gets the
 * `/ws/platform/events` frames it would send (db_changed after a write that
 * changed rows).
 */
export function stubPlatform(appDir = FIXTURE, { instanceId = INSTANCE_ID } = {}) {
  const db = new DatabaseSync(':memory:');
  db.exec(fs.readFileSync(path.join(appDir, 'schema.sql'), 'utf8'));
  const listeners = new Set();
  const calls = [];
  const changed = (changes) => changes && listeners.forEach((fn) => fn({ type: 'db_changed', instance_id: instanceId }));
  const run = (sql, params) => {
    const r = db.prepare(sql).run(...bind(params));
    return { changes: Number(r.changes), lastInsertRowId: Number(r.lastInsertRowid) };
  };
  function rpc(body) {
    calls.push(body);
    try {
      if (body.op === 'getAll' || body.op === 'getFirst') {
        if (!isRead(body.sql)) return { status: 422, body: { detail: 'sql_not_allowed', message: 'not authorized' } };
        const st = db.prepare(body.sql);
        return { status: 200, body: body.op === 'getAll' ? { rows: st.all(...bind(body.params)) } : { row: st.get(...bind(body.params)) ?? null } };
      }
      if (body.op === 'run') {
        if (!isWrite(body.sql)) return { status: 422, body: { detail: 'sql_not_allowed', message: 'not authorized' } };
        const r = run(body.sql, body.params);
        changed(r.changes);
        return { status: 200, body: r };
      }
      if (body.op === 'action') {
        const file = path.join(appDir, 'actions', `${body.name}.sql`);
        if (!/^[a-z][a-zA-Z0-9_]*$/.test(body.name) || !fs.existsSync(file)) return { status: 404, body: { detail: 'unknown_action' } };
        const out = { changes: 0, lastInsertRowId: 0, rows: [] };
        db.exec('BEGIN');
        try {
          for (const sql of split(fs.readFileSync(file, 'utf8'))) {
            const params = Object.keys(body.params ?? {}).length ? body.params : undefined;
            if (isRead(sql)) out.rows = db.prepare(sql).all(...bind(params));
            else {
              const r = run(sql, params);
              out.changes += r.changes;
              out.lastInsertRowId = r.lastInsertRowId;
            }
          }
          db.exec('COMMIT');
        } catch (err) {
          db.exec('ROLLBACK');
          throw err;
        }
        changed(out.changes);
        return { status: 200, body: out };
      }
      return { status: 422, body: { detail: 'invalid_params' } };
    } catch (err) {
      return { status: 422, body: { detail: 'sql_error', message: err.message } };
    }
  }
  return {
    db,
    calls,
    rpc,
    onEvent: (fn) => (listeners.add(fn), () => listeners.delete(fn)),
    emit: (frame) => listeners.forEach((fn) => fn(frame)),
    /** A write that doesn't come through the sandbox (another client, the agent). */
    externalWrite(sql, params) {
      const r = run(sql, params);
      changed(r.changes);
      return r;
    },
    /** `fetch` for platformForward / fetchBundle against this stub. */
    fetch: (bundle) => async (url, init = {}) => {
      const reply = (status, body) => ({ ok: status < 300, status, json: async () => body, text: async () => JSON.stringify(body) });
      if (url.endsWith(`/instances/${instanceId}/rpc`)) {
        const r = rpc(JSON.parse(init.body));
        return reply(r.status, r.body);
      }
      if (url.endsWith(`/instances/${instanceId}/bundle`)) return reply(200, { app_id: APP_ID, version: '1.0.0', sdk: '1', bundle_id: 'b1', code: await bundle() });
      return reply(404, { detail: 'not_found' });
    },
  };
}
