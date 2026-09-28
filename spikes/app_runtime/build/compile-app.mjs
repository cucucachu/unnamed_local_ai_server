// App compiler: app/** (expo-router file routes) -> one small CJS-in-a-wrapper
// bundle whose only externals are the allowlisted modules the runtime provides.
//
//   node build/compile-app.mjs <appDir> [--out dist/<slug>.app.js] [--dev]
//
// Prints JSON: { ok, diagnostics[], stats } (diagnostics are file:line:col).
import * as esbuild from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';

export const ALLOWED_MODULES = ['react', 'react/jsx-runtime', 'react-native', 'expo-router', 'expo-sqlite', '@homeai/sdk'];
const SOURCE_EXT = new Set(['.ts', '.tsx', '.js', '.jsx', '.json']);
const SEGMENT = /^(index|_layout|[a-z0-9][a-z0-9_-]*|\[[a-zA-Z_][a-zA-Z0-9_]*\]|\[\.\.\.[a-zA-Z_][a-zA-Z0-9_]*\])$/;

export const BANNER = '__homeai_define(function (require, module, exports) {';
export const FOOTER = '});';

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
    const p = path.join(dir, d.name);
    return d.isDirectory() ? walk(p) : [p];
  });
}

/** Scan app/ and build the route table; returns { entrySource, routes, diagnostics }. */
export function routeTable(appRoot) {
  const appDir = path.join(appRoot, 'app');
  const diagnostics = [];
  const routes = [];
  let layout = null;
  for (const abs of walk(appDir).sort()) {
    const rel = path.relative(appDir, abs).split(path.sep).join('/');
    const ext = path.extname(rel);
    if (ext !== '.tsx' && ext !== '.ts') {
      diagnostics.push({ file: `app/${rel}`, line: 1, column: 1, code: 'route-file', message: 'Only .tsx/.ts files may live in app/ (put helpers outside app/)' });
      continue;
    }
    const name = rel.slice(0, -ext.length);
    const parts = name.split('/');
    const bad = parts.find((p) => !SEGMENT.test(p));
    if (bad) {
      diagnostics.push({ file: `app/${rel}`, line: 1, column: 1, code: 'route-name', message: `Unsupported route segment "${bad}" (supported: name, index, [param], [...rest]; groups and nested layouts are not supported yet)` });
      continue;
    }
    if (name === '_layout') layout = rel;
    else if (parts.includes('_layout')) diagnostics.push({ file: `app/${rel}`, line: 1, column: 1, code: 'route-name', message: 'Nested _layout files are not supported yet; only app/_layout.tsx' });
    else routes.push({ name, file: rel, segments: parts.filter((p) => p !== 'index') });
  }
  if (!routes.some((r) => r.segments.length === 0)) {
    diagnostics.push({ file: 'app/index.tsx', line: 1, column: 1, code: 'route-missing-index', message: 'The app needs an app/index.tsx route' });
  }
  const lines = [];
  if (layout) lines.push(`import Layout from ${JSON.stringify('./app/' + layout)};`);
  routes.forEach((r, i) => lines.push(`import R${i} from ${JSON.stringify('./app/' + r.file)};`));
  lines.push(`export const layout = ${layout ? 'Layout' : 'undefined'};`);
  lines.push(
    `export const routes = [${routes.map((r, i) => `{ name: ${JSON.stringify(r.name)}, segments: ${JSON.stringify(r.segments)}, component: R${i} }`).join(', ')}];`,
  );
  return { entrySource: lines.join('\n'), routes, diagnostics };
}

function allowlistPlugin(appRoot) {
  const root = fs.realpathSync(appRoot);
  const inside = (p) => p === root || p.startsWith(root + path.sep);
  const allowed = new Set(ALLOWED_MODULES);
  return {
    name: 'homeai-allowlist',
    setup(build) {
      build.onResolve({ filter: /.*/ }, (args) => {
        if (args.kind === 'entry-point') return;
        if (allowed.has(args.path)) return { path: args.path, external: true };
        if (args.path.startsWith('.') || path.isAbsolute(args.path)) {
          const abs = path.resolve(args.resolveDir, args.path);
          if (!inside(abs)) {
            return { errors: [{ text: `Import "${args.path}" resolves outside the app directory`, detail: 'import-escape' }] };
          }
          return; // default resolution (extensions, index files)
        }
        return {
          errors: [{ text: `Import "${args.path}" is not allowed in apps. Allowed: ${ALLOWED_MODULES.filter((m) => m !== 'react/jsx-runtime').join(', ')}`, detail: 'import-not-allowed' }],
        };
      });
      // Belt and braces: catch symlinks that point outside the app dir, and non-source files.
      build.onLoad({ filter: /.*/ }, (args) => {
        const real = fs.realpathSync(args.path);
        if (!inside(real)) return { errors: [{ text: `File "${path.relative(root, args.path)}" is a link outside the app directory`, detail: 'import-escape' }] };
        if (!SOURCE_EXT.has(path.extname(real))) return { errors: [{ text: `Unsupported file type "${path.extname(real)}"`, detail: 'file-type' }] };
        return;
      });
    },
  };
}

function toDiagnostic(msg, appRoot, severity) {
  const loc = msg.location;
  return {
    severity,
    code: msg.detail || msg.id || 'build',
    file: loc ? path.relative(appRoot, path.resolve(appRoot, loc.file)).split(path.sep).join('/') : null,
    line: loc ? loc.line : null,
    column: loc ? loc.column + 1 : null,
    message: msg.text,
    lineText: loc ? loc.lineText : undefined,
  };
}

export async function compileApp(appRoot, { dev = false } = {}) {
  appRoot = path.resolve(appRoot);
  const t0 = performance.now();
  const rt = routeTable(appRoot);
  if (rt.diagnostics.length) return { ok: false, diagnostics: rt.diagnostics.map((d) => ({ severity: 'error', ...d })), routes: rt.routes };
  let result;
  try {
    result = await esbuild.build({
      stdin: { contents: rt.entrySource, resolveDir: appRoot, sourcefile: '<routes>', loader: 'js' },
      absWorkingDir: appRoot,
      bundle: true,
      write: false,
      outfile: 'app.js',
      format: 'cjs',
      platform: 'browser',
      target: 'es2020',
      jsx: 'automatic',
      minify: !dev,
      sourcemap: 'external',
      sourcesContent: true,
      banner: { js: BANNER },
      footer: { js: FOOTER },
      define: { 'process.env.NODE_ENV': JSON.stringify(dev ? 'development' : 'production') },
      logLevel: 'silent',
      plugins: [allowlistPlugin(appRoot)],
    });
  } catch (err) {
    return {
      ok: false,
      diagnostics: [...(err.errors ?? []).map((m) => toDiagnostic(m, appRoot, 'error')), ...(err.warnings ?? []).map((m) => toDiagnostic(m, appRoot, 'warning'))],
      routes: rt.routes,
    };
  }
  const js = result.outputFiles.find((f) => f.path.endsWith('.js'));
  const map = result.outputFiles.find((f) => f.path.endsWith('.map'));
  const code = js.text;
  return {
    ok: true,
    code,
    map: map.text,
    routes: rt.routes.map(({ name, segments }) => ({ name, segments })),
    diagnostics: result.warnings.map((m) => toDiagnostic(m, appRoot, 'warning')),
    stats: { bytes: Buffer.byteLength(code), gzip: zlib.gzipSync(code, { level: 9 }).length, compileMs: +(performance.now() - t0).toFixed(1) },
  };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const args = process.argv.slice(2);
  const appDir = args.find((a) => !a.startsWith('--'));
  const outIdx = args.indexOf('--out');
  const res = await compileApp(appDir, { dev: args.includes('--dev') });
  if (res.ok && outIdx >= 0) {
    fs.mkdirSync(path.dirname(args[outIdx + 1]), { recursive: true });
    fs.writeFileSync(args[outIdx + 1], res.code);
    fs.writeFileSync(args[outIdx + 1] + '.map', res.map);
  }
  const { code, map, ...summary } = res;
  console.log(JSON.stringify(summary, null, 2));
  process.exitCode = res.ok ? 0 : 1;
}
