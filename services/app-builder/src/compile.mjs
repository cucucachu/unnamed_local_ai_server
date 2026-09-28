// Import allowlist + bundle: app/** -> one CJS body wrapped as
// `__homeai_define(function (require, module, exports) {…})` whose only
// `require()`s are the allowed modules the runtime provides (docs/PLATFORM.md
// §7 "Runtime and bridge"). No app code runs here.
import * as esbuild from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { diagnostic, relative } from './diagnostics.mjs';
import { routeTable } from './routes.mjs';
import { ALLOWED_MODULES } from './sdk.mjs';

export { ALLOWED_MODULES };
const LISTED = ALLOWED_MODULES.filter((m) => m !== 'react/jsx-runtime').join(', ');
const SOURCE_EXT = new Set(['.ts', '.tsx', '.js', '.jsx', '.json']);

export const BANNER = '__homeai_define(function (require, module, exports) {';
export const FOOTER = '});';

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
          if (!inside(path.resolve(args.resolveDir, args.path))) {
            return { errors: [{ text: `Import "${args.path}" points outside the app folder; apps can only import their own files`, detail: 'import' }] };
          }
          return;
        }
        return { errors: [{ text: `Import "${args.path}" is not allowed in apps. Allowed modules: ${LISTED}, and the app's own files`, detail: 'import' }] };
      });
      build.onLoad({ filter: /.*/ }, (args) => {
        const real = fs.realpathSync(args.path);
        if (!inside(real)) return { errors: [{ text: `${relative(root, args.path)} is a link outside the app folder`, detail: 'import' }] };
        if (!SOURCE_EXT.has(path.extname(real))) {
          return { errors: [{ text: `${relative(root, args.path)}: only .ts, .tsx, .js, .jsx and .json files can be imported`, detail: 'import' }] };
        }
        return;
      });
    },
  };
}

function toDiagnostic(msg, appRoot) {
  const loc = msg.location;
  const step = msg.detail === 'import' || msg.text.startsWith('Could not resolve') ? 'import' : 'bundle';
  let text = msg.text;
  if (text.startsWith('Could not resolve')) text += ' (check the path and file name; imports are relative to the importing file)';
  return diagnostic({
    step,
    file: loc ? relative(appRoot, loc.file) : '',
    line: loc ? loc.line : null,
    column: loc ? loc.column + 1 : null,
    message: text,
    source: loc?.lineText,
  });
}

async function bundle(appRoot, entrySource, dev) {
  return esbuild.build({
    stdin: { contents: entrySource, resolveDir: appRoot, sourcefile: '<routes>', loader: 'js' },
    absWorkingDir: appRoot,
    // So the plugin's onLoad sees the link's own path and names it.
    preserveSymlinks: true,
    bundle: true,
    write: false,
    outfile: path.join(appRoot, 'app.js'),
    format: 'cjs',
    platform: 'browser',
    target: 'es2020',
    jsx: 'automatic',
    // Otherwise esbuild drops unused imports as possibly type-only, and the
    // allowlist never sees them.
    tsconfigRaw: { compilerOptions: { verbatimModuleSyntax: true } },
    minify: !dev,
    sourcemap: 'external',
    sourcesContent: true,
    banner: { js: BANNER },
    footer: { js: FOOTER },
    define: { 'process.env.NODE_ENV': JSON.stringify(dev ? 'development' : 'production') },
    logLevel: 'silent',
    plugins: [allowlistPlugin(appRoot)],
  });
}

function outputs(result) {
  const js = result.outputFiles.find((f) => f.path.endsWith('.js'));
  const map = result.outputFiles.find((f) => f.path.endsWith('.map'));
  return { code: js.text, map: map.text };
}

/**
 * { ok, diagnostics, routes, prod: {code, map}, dev: {code, map}, stats }.
 * `prod` is what the platform stores; `dev` (unminified, development React
 * warnings) is what the smoke render evaluates.
 */
export async function compileApp(appRoot) {
  appRoot = path.resolve(appRoot);
  const t0 = performance.now();
  const rt = routeTable(appRoot);
  const routes = rt.routes;
  if (rt.diagnostics.length) return { ok: false, diagnostics: rt.diagnostics, routes };
  let prod;
  let dev;
  try {
    prod = outputs(await bundle(appRoot, rt.entrySource, false));
    dev = outputs(await bundle(appRoot, rt.entrySource, true));
  } catch (err) {
    if (!err.errors) throw err;
    return { ok: false, diagnostics: err.errors.map((m) => toDiagnostic(m, appRoot)), routes };
  }
  return {
    ok: true,
    diagnostics: [],
    routes,
    prod,
    dev,
    stats: {
      bytes: Buffer.byteLength(prod.code),
      gzip: zlib.gzipSync(prod.code, { level: 9 }).length,
      bundle_ms: +(performance.now() - t0).toFixed(1),
    },
  };
}
