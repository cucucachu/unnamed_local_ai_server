// Stand-in for the platform + host web app (NOT the live stack): serves the
// host page, the runtime, compiled app bundles, and an RPC endpoint backed by
// node:sqlite. Records every request so tests can see what the sandbox sent.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath } from 'node:url';
import { compileApp } from '../build/compile-app.mjs';

const spike = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const STATIC = {
  '/': ['host/host.html', 'text/html'],
  '/host/host.js': ['host/host.js', 'text/javascript'],
  '/build/sandbox-html.mjs': ['build/sandbox-html.mjs', 'text/javascript'],
  '/dist/runtime.js': ['dist/runtime.js', 'text/javascript'],
};
export const SESSION = 'hs_test_session_secret';

function freshDb(appDir) {
  const db = new DatabaseSync(':memory:');
  db.exec(fs.readFileSync(path.join(appDir, 'schema.sql'), 'utf8'));
  const ins = db.prepare('INSERT INTO items (name) VALUES (?)');
  for (const n of ['Milk', 'Eggs', 'Bread']) ins.run(n);
  return db;
}

export function startServer({ port = 0, host = '127.0.0.1', expoDist = null } = {}) {
  const log = [];
  let db = freshDb(path.join(spike, 'apps/groceries'));
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://x');
    const cookie = req.headers.cookie ?? '';
    log.push({ method: req.method, path: url.pathname + url.search, origin: req.headers.origin ?? null, hasSession: cookie.includes(SESSION) });
    const body = await new Promise((r) => {
      let b = '';
      req.on('data', (c) => (b += c));
      req.on('end', () => r(b));
    });
    const json = (code, obj, headers = {}) => {
      res.writeHead(code, { 'content-type': 'application/json', ...headers });
      res.end(JSON.stringify(obj));
    };
    const candidate = expoDist && !url.pathname.startsWith('/api/') && path.join(expoDist, url.pathname === '/' ? 'index.html' : path.normalize(url.pathname));
    const expoFile = candidate && candidate.startsWith(expoDist) && fs.existsSync(candidate) ? candidate : null;
    if (STATIC[url.pathname] || expoFile) {
      const [file, type] = expoFile ? [path.relative(spike, expoFile), expoFile.endsWith('.html') ? 'text/html' : 'text/javascript'] : STATIC[url.pathname];
      const headers = { 'content-type': type, 'cache-control': 'no-store' };
      // A non-HttpOnly cookie too, so "document.cookie is unreadable in the sandbox" is meaningful.
      if (url.pathname === '/') headers['set-cookie'] = [`homeai_session=${SESSION}; Path=/; HttpOnly; SameSite=Lax`, 'homeai_visible=readable-by-host; Path=/; SameSite=Lax'];
      res.writeHead(200, headers);
      return res.end(fs.readFileSync(path.join(spike, file)));
    }
    if (url.pathname === '/api/bundle') {
      const appDir = path.join(spike, 'apps', url.searchParams.get('app') ?? 'groceries');
      const out = await compileApp(appDir);
      if (!out.ok) return json(422, out);
      res.writeHead(200, { 'content-type': 'text/javascript', 'x-compile-ms': String(out.stats.compileMs) });
      return res.end(out.code);
    }
    if (url.pathname === '/api/secret') {
      return json(cookie.includes(SESSION) ? 200 : 401, { secret: cookie.includes(SESSION) ? 'top-secret' : null });
    }
    if (url.pathname.startsWith('/api/rpc/')) {
      if (!cookie.includes(SESSION)) return json(401, { ok: false, error: { code: 'unauthenticated', message: 'no session' } });
      try {
        const { method, params } = JSON.parse(body);
        const stmt = db.prepare(params.sql);
        const args = params.params ?? [];
        let result;
        if (method === 'db.getAll') result = stmt.all(...args);
        else if (method === 'db.getFirst') result = stmt.get(...args) ?? null;
        else if (method === 'db.run') {
          const r = stmt.run(...args);
          result = { changes: Number(r.changes), lastInsertRowId: Number(r.lastInsertRowid) };
        } else return json(400, { ok: false, error: { code: 'method-not-allowed', message: method } });
        return json(200, { ok: true, result });
      } catch (err) {
        return json(200, { ok: false, error: { code: 'sql', message: err.message } });
      }
    }
    if (url.pathname === '/api/_reset') {
      db = freshDb(path.join(spike, 'apps/groceries'));
      log.length = 0;
      return json(200, { ok: true });
    }
    json(404, { error: 'not found' });
  });
  return new Promise((resolve) => server.listen(port, host, () => resolve({ server, log, url: `http://${host}:${server.address().port}` })));
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const expoDist = process.argv.includes('--expo') ? path.join(spike, 'expo-host/dist') : null;
  const { url } = await startServer({ port: Number(process.env.PORT ?? 8787), expoDist });
  console.log(`host stand-in on ${url}`);
}
