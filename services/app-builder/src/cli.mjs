// Entry point of a build container (docs/ARCHITECTURE.md §3 "App builds").
//
//   node src/cli.mjs compile [--src /src] [--out /out]
//       routes -> import allowlist + bundle -> type-check. Writes
//       result.json, and when ok also app.js(.map) (stored by the platform)
//       and app.dev.js(.map) (for the smoke render).
//   node src/cli.mjs smoke [--src /src] [--bundle /bundle] [--out /out]
//       renders every route of the compile phase's dev bundle; writes result.json.
//
// Exits 0 whenever it wrote result.json, whatever the app's diagnostics.
import fs from 'node:fs';
import path from 'node:path';
import { compileApp } from './compile.mjs';
import { diagnostic, writeResult } from './diagnostics.mjs';
import { smokeRender } from './smoke.mjs';
import { typecheckApp } from './typecheck.mjs';

function option(args, name, fallback) {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : fallback;
}

export async function compilePhase(src, out) {
  const compiled = await compileApp(src);
  if (!compiled.ok) return { ok: false, diagnostics: compiled.diagnostics, routes: compiled.routes };
  const typed = typecheckApp(src);
  const result = { ok: typed.diagnostics.length === 0, diagnostics: typed.diagnostics, routes: compiled.routes, stats: { ...compiled.stats, typecheck_ms: typed.ms } };
  if (result.ok) {
    fs.writeFileSync(path.join(out, 'app.js'), compiled.prod.code);
    fs.writeFileSync(path.join(out, 'app.js.map'), compiled.prod.map);
    fs.writeFileSync(path.join(out, 'app.dev.js'), compiled.dev.code);
    fs.writeFileSync(path.join(out, 'app.dev.js.map'), compiled.dev.map);
  }
  return result;
}

export async function smokePhase(src, bundle) {
  const compiled = JSON.parse(fs.readFileSync(path.join(bundle, 'result.json'), 'utf8'));
  if (!compiled.ok) throw new Error('the compile phase did not succeed');
  const app = { code: fs.readFileSync(path.join(bundle, 'app.dev.js'), 'utf8'), map: fs.readFileSync(path.join(bundle, 'app.dev.js.map'), 'utf8') };
  return smokeRender(src, app, compiled.routes);
}

async function main(args) {
  const phase = args[0];
  const src = option(args, 'src', '/src');
  const out = option(args, 'out', '/out');
  if (phase !== 'compile' && phase !== 'smoke') {
    console.error('usage: cli.mjs compile|smoke [--src DIR] [--bundle DIR] [--out DIR]');
    return 2;
  }
  try {
    const result = phase === 'compile' ? await compilePhase(src, out) : await smokePhase(src, option(args, 'bundle', '/bundle'));
    writeResult(out, result);
  } catch (err) {
    console.error(err?.stack ?? err);
    writeResult(out, { ok: false, diagnostics: [diagnostic({ step: 'build', message: `the builder failed during ${phase}: ${err?.message ?? err}` })] });
  }
  return 0;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = await main(process.argv.slice(2));
}
