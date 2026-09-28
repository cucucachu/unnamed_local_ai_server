// Experiment 1: bundle sizes, compile time, and import-allowlist diagnostics.
//   node tests/compile.e2e.mjs      -> results/compile.json
import path from 'node:path';
import { buildRuntime } from '../build/build-runtime.mjs';
import { compileApp } from '../build/compile-app.mjs';
import { check, spike, stats, writeResult } from './lib.mjs';

const results = [];
const runtime = await buildRuntime();
const curated = await buildRuntime({ curated: true });
const app = await compileApp(path.join(spike, 'apps/groceries'));
const times = [];
for (let i = 0; i < 20; i++) times.push((await compileApp(path.join(spike, 'apps/groceries'))).stats.compileMs);

const externals = [...app.code.matchAll(/require\("([^"]+)"\)/g)].map((m) => m[1]);
check(results, 'app bundle only requires allowlisted runtime modules', externals.every((m) => ['react', 'react/jsx-runtime', 'react-native', 'expo-router', 'expo-sqlite', '@homeai/sdk'].includes(m)), [...new Set(externals)]);
check(results, 'app bundle contains no React/RN code (externals really external)', app.stats.bytes < 10_000, app.stats);
check(results, 'route table generated from app/**', JSON.stringify(app.routes.map((r) => r.name)) === '["index","item/[id]"]', app.routes);

const bad = await compileApp(path.join(spike, 'apps/bad-imports'));
const want = [
  ['app/index.tsx', 2, 30, 'react-dom'],
  ['app/index.tsx', 4, 21, '../../groceries/schema.sql'],
  ['app/index.tsx', 6, 20, '"fs"'],
  ['lib/helper.ts', 1, 16, 'node:os'],
];
for (const [file, line, column, text] of want) {
  const d = bad.diagnostics.find((x) => x.file === file && x.line === line && x.column === column && x.message.includes(text));
  check(results, `allowlist rejects ${text} at ${file}:${line}:${column}`, d, d?.message);
}
const link = await compileApp(path.join(spike, 'apps/bad-symlink'));
check(results, 'symlink escaping the app dir is rejected', !link.ok && link.diagnostics[0].message.includes('link outside'), link.diagnostics[0]);

const out = {
  runtime: runtime.stats,
  runtimeCuratedRN: { bytes: curated.stats.bytes, gzip: curated.stats.gzip, brotli: curated.stats.brotli },
  app: app.stats,
  appCompileMs: stats(times),
  diagnostics: { badImports: bad.diagnostics, badSymlink: link.diagnostics },
  checks: results,
};
writeResult('compile', out);
console.log(JSON.stringify({ runtime: runtime.stats, curated: out.runtimeCuratedRN, app: app.stats, appCompileMs: out.appCompileMs }, null, 2));
const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exitCode = failed.length ? 1 : 0;
