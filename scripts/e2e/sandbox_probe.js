// M12-08: escape probes and an RPC bench, run INSIDE a running app's sandbox
// document with the same privileges as app code (the M12-01 spike's
// probes, against the real runtime and host).
//
// - scripts/verify_tenancy.sh (check 20, via app_sandbox_tenancy.mjs)
//   evaluates it in the web runner's `<iframe sandbox="allow-scripts">`.
// - On a phone (docs/HOST-CHECKS.md, M12): open an app in Expo Go, attach
//   the WebView devtools (Android: chrome://inspect; iOS: Safari -> Develop),
//   paste this whole file into the sandbox document's console, then run
//     await homeaiSandboxProbe('http://<host>')
//   and copy the JSON it prints.
//
// Nothing here is app API: the bench speaks bridge envelope v1
// (docs/PLATFORM.md §7 "Bridge protocol") directly, with ids the runtime
// never uses, so the app keeps running. It only reads (`db.getAll` of
// `SELECT 1`), so a viewer can run it too.
async function homeaiSandboxProbe(hostUrl, { bench = 200, navigate = false } = {}) {
  const host = String(hostUrl).replace(/\/$/, '');
  const report = { origin: null, bench: null, probes: {}, escaped: [] };

  // --- RPC bench over the bridge ---------------------------------------------
  const waiting = new Map();
  const take = (raw) => {
    let env;
    try {
      env = JSON.parse(typeof raw === 'string' ? raw : '');
    } catch {
      return false;
    }
    const w = env && env.kind === 'res' ? waiting.get(env.id) : undefined;
    if (!w) return false;
    waiting.delete(env.id);
    w(env);
    return true;
  };
  const onMessage = (e) => {
    if (e.source === window.parent) take(e.data);
  };
  const nativeReceive = window.__homeaiReceive;
  window.addEventListener('message', onMessage);
  if (window.ReactNativeWebView && nativeReceive) window.__homeaiReceive = (m) => take(m) || nativeReceive(m);
  const send = (wire) => (window.ReactNativeWebView ? window.ReactNativeWebView.postMessage(wire) : window.parent.postMessage(wire, '*'));
  const request = (id, method, params) =>
    new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        waiting.delete(id);
        reject(new Error(`${method}: no answer in 15 s`));
      }, 15000);
      waiting.set(id, (env) => {
        clearTimeout(timer);
        resolve(env);
      });
      send(JSON.stringify({ homeai: 1, kind: 'req', id, method, params }));
    });
  try {
    const times = [];
    let failures = 0;
    for (let i = 0; i < bench; i++) {
      const t0 = Date.now();
      const env = await request(1_000_000_000 + i, 'db.getAll', { sql: 'SELECT 1 AS one', params: [] });
      times.push(Date.now() - t0);
      if (!env.ok || !Array.isArray(env.result) || env.result[0]?.one !== 1) failures++;
    }
    times.sort((a, b) => a - b);
    const pct = (p) => times[Math.min(times.length - 1, Math.floor((p / 100) * times.length))];
    report.bench = times.length ? { n: times.length, failures, p50: pct(50), p95: pct(95), max: times[times.length - 1] } : null;
  } catch (e) {
    report.bench = { error: String(e && e.message) };
  } finally {
    window.removeEventListener('message', onMessage);
    if (nativeReceive) window.__homeaiReceive = nativeReceive;
  }

  // --- escape probes -----------------------------------------------------------
  // `escaped` lists the probes that got something they must not: the host's
  // cookies or storage, the parent's document, a network response, a window.
  const probe = async (name, fn, escapedIf = (r) => r.ok, timeoutMs = 4000) => {
    let result;
    try {
      const value = await Promise.race([fn(), new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), timeoutMs))]);
      result = { ok: true, value: String(value).slice(0, 160) };
    } catch (e) {
      result = { ok: false, error: `${e && e.name}: ${e && e.message}`.slice(0, 200) };
    }
    report.probes[name] = result;
    if (escapedIf(result)) report.escaped.push(name);
  };
  const nonEmpty = (r) => r.ok && r.value !== '';
  const holdsKeys = (r) => r.ok && r.value !== '0';
  const parentOf = () => {
    if (window.parent === window) throw new Error('top-level document (WebView): no parent');
    return window.parent;
  };
  // A WebView's document is top-level and the native host keeps its session
  // outside the web view, so only the web frame's origin must be opaque.
  report.origin = String(self.origin);
  if (window.parent !== window && report.origin !== 'null') report.escaped.push('origin');
  await probe('document.cookie', () => document.cookie, nonEmpty);
  await probe('localStorage (keys already there)', () => localStorage.length, holdsKeys);
  await probe('sessionStorage (keys already there)', () => sessionStorage.length, holdsKeys);
  await probe(
    'indexedDB',
    () =>
      new Promise((res, rej) => {
        const q = indexedDB.open('homeai-probe');
        q.onsuccess = () => res('opened');
        q.onerror = () => rej(q.error);
      }),
  );
  await probe('parent.document', () => parentOf().document.cookie);
  await probe('parent.localStorage', () => parentOf().localStorage.length);
  await probe('fetch /api/platform/me (credentials: include)', async () => {
    const r = await fetch(`${host}/api/platform/me`, { credentials: 'include' });
    return `${r.status} ${(await r.text()).slice(0, 80)}`;
  });
  await probe('fetch no-cors', async () => `opaque ${(await fetch(`${host}/api/platform/me?no-cors`, { mode: 'no-cors', credentials: 'include' })).type}`);
  await probe(
    'XMLHttpRequest',
    () =>
      new Promise((res, rej) => {
        const x = new XMLHttpRequest();
        x.open('GET', `${host}/api/platform/me?xhr`);
        x.withCredentials = true;
        x.onload = () => res(`${x.status}`);
        x.onerror = () => rej(new Error('xhr error'));
        x.send();
      }),
  );
  await probe(
    'WebSocket /ws/platform/events',
    () =>
      new Promise((res, rej) => {
        const ws = new WebSocket(`${host.replace(/^http/, 'ws')}/ws/platform/events`);
        ws.onopen = () => (ws.close(), res('open'));
        ws.onerror = () => rej(new Error('ws error'));
      }),
  );
  await probe(
    'image beacon',
    () =>
      new Promise((res, rej) => {
        const i = new Image();
        i.onload = () => res('loaded');
        i.onerror = () => rej(new Error('img error'));
        i.src = `${host}/api/platform/me?beacon`;
      }),
  );
  // sendBeacon reports `true` (queued) even when the CSP then blocks the
  // request, so only a network log can tell (app_sandbox_tenancy.mjs has one).
  await probe('navigator.sendBeacon', () => (navigator.sendBeacon(`${host}/api/platform/me?send-beacon`, 'x') ? 'queued' : 'refused'), () => false);
  await probe('window.open', () => (window.open(`${host}/?popup`) ? 'opened' : 'refused'), (r) => r.ok && r.value === 'opened');
  if (navigate) {
    // Last: on web the host removes a frame that navigates itself.
    await probe('top.location', () => ((window.top.location.href = `${host}/?pwned-top`), 'assigned'), () => false);
  }
  console.log(JSON.stringify(report, null, 2));
  return report;
}
