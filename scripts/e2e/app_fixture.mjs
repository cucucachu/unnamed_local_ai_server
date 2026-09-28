#!/usr/bin/env node
// M12-06: the SDK's fixture app (packages/homeai-sdk/tests/fixtures/runtime-check)
// for trying the app runner by hand, e.g. the Expo Go check in
// docs/HOST-CHECKS.md (M12). As YOUR user, through Caddy with a native
// bearer session (signed out again at the end):
//
//   node scripts/e2e/app_fixture.mjs install --user <you> [--space <slug>]
//       upload to <space>/Apps/runtime-check, register, install there, build (v1)
//   node scripts/e2e/app_fixture.mjs v2 --user <you> [--space <slug>]
//       rebuild as v2: the build label reads v2 and a "Crash" button appears
//       (it throws during render, for the runner's error overlay)
//   node scripts/e2e/app_fixture.mjs v1 --user <you> [--space <slug>]
//       rebuild the original
//   node scripts/e2e/app_fixture.mjs uninstall --user <you> [--space <slug>]
//       uninstall the instance (the platform keeps its data in apps/.trash)
//
// --space defaults to your Personal space; a shared space's slug installs
// it there (you must be an owner or editor). --base defaults to
// $HOMEAI_BASE_URL or http://homeai.local. The password comes from
// $HOMEAI_PASSWORD or is prompted for (not echoed).
//
// app_runner_browser_smoke.mjs imports `fixtureV2` from here.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
export const FIXTURE = path.resolve(SCRIPT_DIR, '../../packages/homeai-sdk/tests/fixtures/runtime-check');
export const FIXTURE_SLUG = 'runtime-check';

export function fixtureFiles(dir = FIXTURE, prefix = '') {
  return fs.readdirSync(path.join(dir, prefix), { withFileTypes: true }).flatMap((d) => {
    const rel = path.posix.join(prefix, d.name);
    return d.isDirectory() ? fixtureFiles(dir, rel) : [rel];
  });
}

/** app/index.tsx as build v2, with a Crash button that throws during render. */
export function fixtureV2(index = fs.readFileSync(path.join(FIXTURE, 'app/index.tsx'), 'utf8')) {
  const v2 = index
    .replace("export const BUILD = 'v1';", "export const BUILD = 'v2';")
    .replace(
      "const [status, setStatus] = useState('');",
      "const [status, setStatus] = useState('');\n  const [crashed, setCrashed] = useState(false);\n  if (crashed) throw new Error('e2e runner crash');",
    )
    .replace(
      '<Text testID="status">',
      '<Pressable testID="crash" onPress={() => setCrashed(true)}>\n        <Text>Crash</Text>\n      </Pressable>\n      <Text testID="status">',
    );
  if (!v2.includes("'v2'") || !v2.includes('testID="crash"') || !v2.includes('e2e runner crash')) {
    throw new Error('the runtime-check fixture changed; update fixtureV2 in scripts/e2e/app_fixture.mjs');
  }
  return v2;
}

function parseArgs(argv) {
  const [command, ...rest] = argv;
  const opts = { command, base: process.env.HOMEAI_BASE_URL ?? 'http://homeai.local', user: null, space: null };
  for (let i = 0; i < rest.length; i += 2) {
    const key = rest[i]?.replace(/^--/, '');
    if (!['base', 'user', 'space'].includes(key) || rest[i + 1] === undefined) throw new Error(`unknown or incomplete option ${rest[i]}`);
    opts[key] = rest[i + 1];
  }
  if (!['install', 'v1', 'v2', 'uninstall'].includes(command) || !opts.user) {
    throw new Error('usage: node scripts/e2e/app_fixture.mjs install|v2|v1|uninstall --user <name> [--space <slug>] [--base <url>]');
  }
  opts.base = opts.base.replace(/\/$/, '');
  return opts;
}

async function readPassword() {
  if (process.env.HOMEAI_PASSWORD) return process.env.HOMEAI_PASSWORD;
  const { stdin, stderr } = process;
  stderr.write('Password: ');
  if (!stdin.isTTY) {
    const chunks = [];
    for await (const chunk of stdin) chunks.push(chunk);
    return Buffer.concat(chunks).toString('utf8').split('\n')[0];
  }
  stdin.setRawMode(true);
  stdin.resume();
  let value = '';
  return new Promise((resolve, reject) => {
    stdin.on('data', function onData(buf) {
      for (const ch of buf.toString('utf8')) {
        if (ch === '\r' || ch === '\n') {
          stdin.setRawMode(false);
          stdin.pause();
          stdin.off('data', onData);
          stderr.write('\n');
          return resolve(value);
        }
        if (ch === '\u0003') return reject(new Error('cancelled'));
        value = ch === '\u007f' ? value.slice(0, -1) : value + ch;
      }
    });
  });
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const password = await readPassword();
  let bearer = null;
  const call = async (method, url, { json, raw, allow = [] } = {}) => {
    const headers = { 'X-HomeAI-Client': 'native', ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}) };
    let body;
    if (raw !== undefined) {
      headers['Content-Type'] = 'application/octet-stream';
      body = raw;
    } else if (json !== undefined) {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(json);
    }
    const r = await fetch(`${opts.base}${url}`, { method, headers, body });
    const text = await r.text();
    const doc = text ? JSON.parse(text) : null;
    if (!r.ok && !allow.includes(doc?.detail)) throw new Error(`${method} ${url}: ${r.status} ${text}`);
    return r.ok ? doc : { error: doc?.detail };
  };

  bearer = (await call('POST', '/api/auth/login', { json: { username: opts.user, password, device_label: 'app_fixture.mjs' } })).session_token;
  try {
    const spaces = (await call('GET', '/api/platform/spaces')).spaces;
    const space = opts.space ? spaces.find((s) => s.slug === opts.space) : spaces.find((s) => s.kind === 'personal');
    if (!space) throw new Error(`no space ${opts.space ?? '(personal)'} for ${opts.user}`);
    const root = space.kind === 'personal' ? '/personal' : `/spaces/${space.slug}`;
    const dir = `${root}/Apps/${FIXTURE_SLUG}`;
    const put = (rel, content) => call('PUT', `/api/platform/files/content?path=${encodeURIComponent(`${dir}/${rel}`)}`, { raw: content });
    const findApp = async () => (await call('GET', '/api/platform/apps')).apps.find((a) => a.source_path === dir);
    const findInstance = async (app) => (await call('GET', `/api/platform/spaces/${space.id}/instances`)).instances.find((i) => i.app_id === app.id);
    const build = async (app) => {
      const r = await call('POST', `/api/platform/apps/${app.id}/build`, { json: {} });
      if (!r.ok) throw new Error(`build failed: ${JSON.stringify(r.diagnostics, null, 2)}`);
      console.log(`built ${app.name} (${r.build.bundle_bytes} B)`);
    };

    if (opts.command === 'install') {
      for (const rel of fixtureFiles()) await put(rel, fs.readFileSync(path.join(FIXTURE, rel)));
      const registered = await call('POST', '/api/platform/apps', { json: { source_path: dir }, allow: ['app_exists'] });
      const app = registered.error ? await findApp() : registered.app;
      const installed = await call('POST', `/api/platform/spaces/${space.id}/instances`, { json: { app_id: app.id }, allow: ['already_installed'] });
      const instance = installed.error ? await findInstance(app) : installed;
      await build(app);
      console.log(`installed in ${space.name}: open Apps -> ${app.name} (instance ${instance.id}, source ${dir})`);
      return;
    }
    const app = await findApp();
    if (!app) throw new Error(`nothing registered at ${dir}; run install first`);
    if (opts.command === 'uninstall') {
      const instance = await findInstance(app);
      if (!instance) throw new Error(`${app.name} isn't installed in ${space.name}`);
      await call('DELETE', `/api/platform/spaces/${space.id}/instances/${instance.id}`);
      console.log(`uninstalled ${app.name} from ${space.name} (source files left in ${dir})`);
      return;
    }
    const original = fs.readFileSync(path.join(FIXTURE, 'app/index.tsx'), 'utf8');
    await put('app/index.tsx', Buffer.from(opts.command === 'v2' ? fixtureV2(original) : original));
    await build(app);
  } finally {
    await call('POST', '/api/auth/logout').catch(() => {});
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  main().catch((err) => {
    console.error(err.message);
    process.exit(1);
  });
}
