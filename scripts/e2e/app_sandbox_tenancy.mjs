// M12-08: docs/PLATFORM.md §9 invariant 7, the sandbox half — for
// scripts/verify_tenancy.sh check 20. Opens an installed instance in the web
// app's runner (Apps tab route /apps/<id>) in headless Chromium, signed in
// with a session cookie, and checks that the sandbox holds no credentials:
//   - the frame is `sandbox="allow-scripts"` (no allow-same-origin), and its
//     document's CSP has `default-src 'none'` and `connect-src 'none'`;
//   - neither the srcdoc nor the live sandbox document contains any of the
//     given secrets (session cookie, identity/delegation tokens) or a marker
//     the host page put in its own localStorage;
//   - inside the frame (sandbox_probe.js): the origin is opaque, cookies,
//     storage and the parent's document are out of reach, fetch / XHR /
//     WebSocket / image / beacon / popup all fail, and no request from the
//     frame gets a response; the bridge still answers;
//   - a bridge request that names another instance (in params or the
//     envelope) still reads this instance's rows only, and a method outside
//     the allowlist is refused.
// Prints `ok   ...` / `FAIL ...` lines (never a secret) and exits 1 on any FAIL.
//
// Env: TEN_BASE (default http://localhost), TEN_COOKIE (`homeai_session=...`),
// TEN_INSTANCE (the instance to open), TEN_OTHER_INSTANCE (another one the
// same user can read), TEN_MARK (a row name in TEN_INSTANCE's items),
// TEN_OTHER_MARK (one only in TEN_OTHER_INSTANCE's), TEN_SECRETS
// (newline-separated strings that must not reach the sandbox).
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const env = (name) => {
  const v = process.env[name];
  if (!v) throw new Error(`${name} is required`);
  return v;
};
const BASE = (process.env.TEN_BASE ?? 'http://localhost').replace(/\/$/, '');
const COOKIE = env('TEN_COOKIE');
const INSTANCE = env('TEN_INSTANCE');
const OTHER = env('TEN_OTHER_INSTANCE');
const MARK = env('TEN_MARK');
const OTHER_MARK = env('TEN_OTHER_MARK');
const SECRETS = env('TEN_SECRETS').split('\n').filter(Boolean);
const PROBE = fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), 'sandbox_probe.js'), 'utf8');
const APP_FRAME = 'iframe[title="app"]';
const UI_TIMEOUT = 30_000;

let failures = 0;
function check(cond, what, detail) {
  if (cond) {
    console.log(`ok   ${what}`);
  } else {
    failures++;
    console.log(`FAIL ${what}${detail === undefined ? '' : `: ${JSON.stringify(detail).slice(0, 600)}`}`);
  }
}
/** Which secrets (by position, never the value) occur in `text`. */
const leaked = (text) => SECRETS.flatMap((s, i) => (text.includes(s) ? [`secret #${i + 1}`] : []));

/** In the sandbox: one raw bridge request, answered by the host. */
async function bridgeRequest(envelope) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve({ timeout: true }), 10_000);
    const onMessage = (e) => {
      let env;
      try {
        env = JSON.parse(e.data);
      } catch {
        return;
      }
      if (e.source !== window.parent || env.kind !== 'res' || env.id !== envelope.id) return;
      clearTimeout(timer);
      window.removeEventListener('message', onMessage);
      resolve(env);
    };
    window.addEventListener('message', onMessage);
    window.parent.postMessage(JSON.stringify(envelope), '*');
  });
}

const [name, ...rest] = COOKIE.split('=');
const browser = await chromium.launch();
try {
  const context = await browser.newContext();
  await context.addCookies([{ name, value: rest.join('='), url: BASE }]);
  const page = await context.newPage();
  const fromSandbox = [];
  // Chromium reports a CSP-blocked request as `request` then `requestfailed`;
  // only one that got a response is an escape.
  page.on('requestfinished', (r) => {
    if (r.frame() !== page.mainFrame()) fromSandbox.push(r.url());
  });

  await page.goto(`${BASE}/`);
  const storageMark = `e2e-tenancy-${crypto.randomBytes(6).toString('hex')}`;
  await page.evaluate((m) => localStorage.setItem('e2e-tenancy-marker', m), storageMark);
  SECRETS.push(storageMark);

  await page.goto(`${BASE}/apps/${INSTANCE}`);
  await page.getByTestId('app-runner').waitFor({ timeout: UI_TIMEOUT });
  await page.frameLocator(APP_FRAME).getByText(MARK, { exact: true }).waitFor({ timeout: UI_TIMEOUT });
  const hostUrl = page.url();
  check(true, `the runner rendered instance ${INSTANCE} (its row is shown)`);

  const frameEl = page.locator(APP_FRAME);
  const sandboxAttr = await frameEl.getAttribute('sandbox');
  check(sandboxAttr === 'allow-scripts', 'the frame is sandbox="allow-scripts" (no allow-same-origin)', sandboxAttr);
  const srcdoc = (await frameEl.getAttribute('srcdoc')) ?? '';
  const csp = /<meta http-equiv="Content-Security-Policy" content="([^"]*)"/i.exec(srcdoc)?.[1] ?? '';
  check(
    csp.includes("default-src 'none'") && csp.includes("connect-src 'none'"),
    "the sandbox document's CSP has default-src 'none' and connect-src 'none'",
    csp,
  );
  check(srcdoc.indexOf('<meta http-equiv="Content-Security-Policy"') < srcdoc.indexOf('<script'), 'the CSP comes before any script');
  check(leaked(srcdoc).length === 0, 'the srcdoc holds no session cookie, token or host storage', leaked(srcdoc));

  const frame = await (await frameEl.elementHandle()).contentFrame();
  const live = await frame.evaluate(() => document.documentElement.outerHTML + JSON.stringify(window.__homeai_config ?? null));
  check(leaked(live).length === 0, 'the live sandbox document and its config hold no credential', leaked(live));

  const report = await frame.evaluate(`${PROBE}\n;homeaiSandboxProbe(${JSON.stringify(BASE)}, { bench: 50 })`);
  check(report.origin === 'null', "the sandbox's origin is opaque", report.origin);
  check(report.escaped.length === 0, `no escape probe succeeded (${Object.keys(report.probes).length} probes)`, {
    escaped: report.escaped,
    probes: Object.fromEntries(report.escaped.map((k) => [k, report.probes[k]])),
  });
  for (const k of ['document.cookie', 'localStorage (keys already there)', 'parent.document', 'fetch /api/platform/me (credentials: include)']) {
    const r = report.probes[k];
    check(r && !r.ok, `${k}: refused (${r?.error ?? r?.value})`);
  }
  check(report.bench?.n === 50 && report.bench.failures === 0, `the bridge answered 50 db.getAll round trips (p50 ${report.bench?.p50} ms, p95 ${report.bench?.p95} ms)`, report.bench);

  const named = await frame.evaluate(bridgeRequest, {
    homeai: 1,
    kind: 'req',
    id: 2_000_000_001,
    method: 'db.getAll',
    instance_id: OTHER,
    params: { sql: 'SELECT name FROM items ORDER BY id', params: [], instanceId: OTHER, instance_id: OTHER, url: `/api/platform/apps/instances/${OTHER}/rpc` },
  });
  const names = (named.result ?? []).map((r) => r.name);
  check(named.ok === true && names.includes(MARK) && !names.includes(OTHER_MARK), "a bridge request naming another instance still reads only this instance's rows", {
    ok: named.ok,
    error: named.error,
    names,
  });
  for (const method of ['rpc', 'fetch', 'db.attach', 'transaction']) {
    const r = await frame.evaluate(bridgeRequest, { homeai: 1, kind: 'req', id: 2_000_000_100, method, params: { sql: 'SELECT 1', instance_id: OTHER } });
    check(r.ok === false && r.error?.code === 'method_not_allowed', `bridge method "${method}" is refused (method_not_allowed)`, r);
  }

  await page.waitForTimeout(500);
  check(fromSandbox.length === 0, 'no request from the sandbox frame got a response', fromSandbox);
  check(page.url() === hostUrl, 'the host page did not navigate', page.url());
  await context.close();
} finally {
  await browser.close();
}
if (failures) {
  console.log(`FAIL ${failures} sandbox check(s) failed`);
  process.exit(1);
}
