// Model-readable build diagnostics (docs/ARCHITECTURE.md §3 "App builds"):
//   { step, file, path, line, column, message, source? }
// `file` is relative to the app folder ("" for the app as a whole), `path`
// is always "" here (it's a JSON pointer for app.json diagnostics, which the
// platform produces), `line`/`column` are 1-based or null, `source` is the
// offending line of code when there is one.
import fs from 'node:fs';
import path from 'node:path';

export const STEPS = ['route', 'import', 'bundle', 'type', 'render', 'sql', 'build'];
export const MAX_DIAGNOSTICS = 50;
const MAX_MESSAGE = 2000;
const MAX_SOURCE = 200;

export function diagnostic({ step, file = '', line = null, column = null, message, source }) {
  const d = { step, file, path: '', line, column, message: String(message).slice(0, MAX_MESSAGE) };
  if (source) d.source = String(source).trim().slice(0, MAX_SOURCE);
  return d;
}

export function capped(diagnostics) {
  return diagnostics.slice(0, MAX_DIAGNOSTICS);
}

export function relative(appRoot, file) {
  return path.relative(appRoot, path.resolve(appRoot, file)).split(path.sep).join('/');
}

export function sourceLine(appRoot, file, line) {
  if (!file || !line) return undefined;
  try {
    return fs.readFileSync(path.join(appRoot, file), 'utf8').split('\n')[line - 1];
  } catch {
    return undefined;
  }
}

export function writeResult(outDir, result) {
  fs.writeFileSync(path.join(outDir, 'result.json'), JSON.stringify({ ...result, diagnostics: capped(result.diagnostics) }));
}
