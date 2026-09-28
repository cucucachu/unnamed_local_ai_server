// The expo-router route table, generated from app/** (docs/PLATFORM.md §7
// "Routes"). The platform's package check (services/platform/app/core/
// manifest.py) enforces the same rules before a build starts; they're
// repeated here so the builder never bundles something the router shim
// can't serve.
import fs from 'node:fs';
import path from 'node:path';
import { diagnostic } from './diagnostics.mjs';

const NAME = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;
const PARAM = /^\[[A-Za-z_][A-Za-z0-9_]*\]$/;
const REST = /^\[\.\.\.[A-Za-z_][A-Za-z0-9_]*\]$/;

function walk(dir) {
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((d) => !d.name.startsWith('.'))
    .flatMap((d) => {
      const p = path.join(dir, d.name);
      return d.isDirectory() ? walk(p) : [p];
    });
}

function segmentProblem(segment, last) {
  if (REST.test(segment)) return last ? null : 'a catch-all [...name] must be the last segment (a file)';
  if (PARAM.test(segment) || NAME.test(segment)) return null;
  return `"${segment}" is not a supported route segment: use a name, index, [param] or [...rest] (groups and special files aren't supported yet)`;
}

/** { entrySource, routes: [{ name, segments, file }], diagnostics } for the app at `appRoot`. */
export function routeTable(appRoot) {
  const appDir = path.join(appRoot, 'app');
  const diagnostics = [];
  const routes = [];
  let layout = null;
  const files = fs.existsSync(appDir) ? walk(appDir).sort() : [];
  for (const abs of files) {
    const rel = path.relative(appDir, abs).split(path.sep).join('/');
    const file = `app/${rel}`;
    const ext = path.extname(rel);
    if ((ext !== '.tsx' && ext !== '.ts') || rel.endsWith('.d.ts')) {
      diagnostics.push(diagnostic({ step: 'route', file, message: `${file}: only .ts/.tsx route files may live under app/ (put components and helpers in another folder, e.g. components/)` }));
      continue;
    }
    const parts = rel.slice(0, -ext.length).split('/');
    if (parts.join('/') === '_layout') {
      layout = rel;
      continue;
    }
    if (parts.includes('_layout')) {
      diagnostics.push(diagnostic({ step: 'route', file, message: `${file}: nested layouts aren't supported yet; only app/_layout.tsx` }));
      continue;
    }
    const problem = parts.map((p, i) => segmentProblem(p, i === parts.length - 1)).find(Boolean);
    if (problem) {
      diagnostics.push(diagnostic({ step: 'route', file, message: `${file}: ${problem}` }));
      continue;
    }
    const segments = parts.filter((p, i) => !(p === 'index' && i === parts.length - 1));
    routes.push({ name: parts.join('/'), segments, file: rel });
  }
  if (!routes.some((r) => r.segments.length === 0)) {
    diagnostics.push(diagnostic({ step: 'route', file: 'app/index.tsx', message: 'app/index.tsx is missing: the app needs a home screen' }));
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

/** A concrete path that opens `route` in the smoke render: params are 1, a catch-all is a/b. */
export function samplePath(route) {
  return '/' + route.segments.map((s) => (s.startsWith('[...') ? 'a/b' : s.startsWith('[') ? '1' : s)).join('/');
}
