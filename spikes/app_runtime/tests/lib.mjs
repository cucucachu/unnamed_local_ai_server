import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildRuntime } from '../build/build-runtime.mjs';

export const spike = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export async function ensureRuntime() {
  const { code, stats } = await buildRuntime();
  fs.mkdirSync(path.join(spike, 'dist'), { recursive: true });
  fs.writeFileSync(path.join(spike, 'dist/runtime.js'), code);
  return { code, stats };
}

export function stats(samples) {
  const s = [...samples].sort((a, b) => a - b);
  const q = (p) => s[Math.min(s.length - 1, Math.floor(p * s.length))];
  const r = (x) => +x.toFixed(3);
  return { n: s.length, p50: r(q(0.5)), p95: r(q(0.95)), p99: r(q(0.99)), max: r(s[s.length - 1]), mean: r(s.reduce((a, b) => a + b, 0) / s.length) };
}

/** Copy an app to a temp dir and apply string replacements to one file (hot-reload variants). */
export function appVariant(app, file, from, to) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'homeai-app-'));
  fs.cpSync(path.join(spike, 'apps', app), dir, { recursive: true });
  const p = path.join(dir, file);
  const src = fs.readFileSync(p, 'utf8');
  if (!src.includes(from)) throw new Error(`variant: "${from}" not in ${file}`);
  fs.writeFileSync(p, src.replace(from, to));
  return dir;
}

export function writeResult(name, data) {
  fs.mkdirSync(path.join(spike, 'results'), { recursive: true });
  fs.writeFileSync(path.join(spike, 'results', `${name}.json`), JSON.stringify(data, null, 2) + '\n');
}

export const check = (results, name, pass, detail) => {
  results.push({ name, pass: !!pass, detail });
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}${detail !== undefined ? '  ' + JSON.stringify(detail) : ''}`);
};
