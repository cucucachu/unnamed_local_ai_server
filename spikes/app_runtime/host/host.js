// Web host side of the bridge. The instance id is fixed here, when the host
// opens the sandbox — never taken from a message.
import { sandboxHtml } from '/build/sandbox-html.mjs';

const qs = new URLSearchParams(location.search);
const INSTANCE = 'inst-123';
const APP = qs.get('app') ?? 'groceries';
const METHODS = new Set(['echo', 'db.getAll', 'db.getFirst', 'db.run', 'action']);
const READ_ONLY = qs.get('role') === 'viewer';

document.getElementById('instance').textContent = INSTANCE;
const status = (s) => (document.getElementById('status').textContent = s);

const events = [];
const waiters = [];
let loads = 0;

const iframe = document.createElement('iframe');
iframe.setAttribute('sandbox', 'allow-scripts');
iframe.title = 'app';
iframe.addEventListener('load', () => {
  // srcdoc fires exactly one load; any further load means the frame navigated
  // itself away (possible exfiltration) — tear it down.
  if (++loads > 1) {
    status('sandbox navigated away — killed');
    events.push({ event: 'host.killed', data: { loads } });
    iframe.remove();
  }
});

function send(env) {
  iframe.contentWindow?.postMessage(JSON.stringify(env), '*');
}
const reply = (id, ok, payload) => send(ok ? { homeai: 1, kind: 'res', id, ok, result: payload } : { homeai: 1, kind: 'res', id, ok, error: payload });

async function handleRequest(env) {
  if (!METHODS.has(env.method)) return reply(env.id, false, { code: 'method-not-allowed', message: env.method });
  if (READ_ONLY && (env.method === 'db.run' || env.method === 'action')) return reply(env.id, false, { code: 'forbidden', message: 'viewer' });
  if (env.method === 'echo') return reply(env.id, true, env.params);
  try {
    const r = await fetch(`/api/rpc/${INSTANCE}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ method: env.method, params: env.params }),
    });
    const body = await r.json();
    reply(env.id, body.ok, body.ok ? body.result : body.error);
    if (body.ok && env.method === 'db.run') send({ homeai: 1, kind: 'evt', event: 'db.changed', data: { instance: INSTANCE } });
  } catch (err) {
    reply(env.id, false, { code: 'host', message: String(err) });
  }
}

window.addEventListener('message', (e) => {
  if (e.source !== iframe.contentWindow) return;
  let env;
  try {
    env = JSON.parse(e.data);
  } catch {
    return;
  }
  if (!env || env.homeai !== 1) return;
  if (env.kind === 'req') handleRequest(env);
  else if (env.kind === 'evt') {
    events.push({ event: env.event, data: env.data, t: performance.now() });
    if (env.event === 'runtime.ready') status(`ready v${env.data.version}`);
    for (const w of [...waiters]) if (w.pred(env)) (waiters.splice(waiters.indexOf(w), 1), w.resolve(env.data));
  }
});

function waitFor(event, pred = () => true, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    const w = { pred: (env) => env.event === event && pred(env.data), resolve };
    waiters.push(w);
    setTimeout(() => reject(new Error(`timeout waiting for ${event}`)), timeoutMs);
  });
}

const [runtime, app] = await Promise.all([
  fetch('/dist/runtime.js').then((r) => r.text()),
  fetch(`/api/bundle?app=${encodeURIComponent(APP)}`).then((r) => r.text()),
]);
const html = sandboxHtml({ runtime, app, config: { initialPath: qs.get('path') ?? '/', debug: true }, ...(qs.get('csp') === '0' ? { csp: '' } : {}) });
iframe.srcdoc = html;
document.body.appendChild(iframe);

// Test/automation surface.
window.homeHost = {
  events,
  send,
  waitFor,
  srcdocBytes: html.length,
  async pushBundle(code) {
    const t0 = performance.now();
    const version = events.filter((e) => e.event === 'runtime.ready').length + 1;
    const ready = waitFor('runtime.ready', (d) => d.version === version);
    send({ homeai: 1, kind: 'evt', event: 'bundle.load', data: { code } });
    const d = await ready;
    return { ms: performance.now() - t0, ...d };
  },
  async bench(n, method = 'echo') {
    const done = waitFor('bench.result', (d) => d.method === method, 60000);
    send({ homeai: 1, kind: 'evt', event: 'bench.run', data: { n, method } });
    return (await done).samples;
  },
};
