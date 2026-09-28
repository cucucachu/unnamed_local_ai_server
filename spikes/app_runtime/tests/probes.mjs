// Runs inside the sandbox (same privileges as app code). Every probe is an
// escape attempt; the result records whether it succeeded or how it failed.
export async function sandboxProbes(hostUrl) {
  const r = {};
  const t = async (name, fn, timeoutMs = 3000) => {
    try {
      const v = await Promise.race([fn(), new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), timeoutMs))]);
      r[name] = { succeeded: true, value: String(v).slice(0, 160) };
    } catch (e) {
      r[name] = { succeeded: false, error: `${e && e.name}: ${e && e.message}`.slice(0, 200) };
    }
  };
  await t('self.origin', () => self.origin);
  await t('document.cookie', () => document.cookie);
  await t('localStorage', () => (localStorage.setItem('x', '1'), localStorage.getItem('x')));
  await t('sessionStorage', () => (sessionStorage.setItem('x', '1'), sessionStorage.getItem('x')));
  await t('indexedDB', () => new Promise((res, rej) => { const q = indexedDB.open('x'); q.onsuccess = () => res('opened'); q.onerror = () => rej(q.error); }));
  await t('parent.document', () => parent.document.title);
  await t('parent.location.href', () => parent.location.href);
  await t('parent.homeHost', () => typeof parent.homeHost.send);
  await t('top.location navigate', () => { top.location.href = hostUrl + '/pwned-top'; return 'assigned (check host URL)'; });
  await t('fetch host /api/secret credentials:include', async () => { const res = await fetch(hostUrl + '/api/secret', { credentials: 'include' }); return `${res.status} ${await res.text()}`; });
  await t('fetch host /api/rpc credentials:include', async () => {
    const res = await fetch(hostUrl + '/api/rpc/inst-123', { method: 'POST', credentials: 'include', body: JSON.stringify({ method: 'db.getAll', params: { sql: 'SELECT * FROM items' } }) });
    return `${res.status} ${await res.text()}`;
  });
  await t('fetch no-cors', async () => { const res = await fetch(hostUrl + '/api/secret?no-cors=1', { mode: 'no-cors', credentials: 'include' }); return `opaque type=${res.type}`; });
  await t('XMLHttpRequest', () => new Promise((res, rej) => { const x = new XMLHttpRequest(); x.open('GET', hostUrl + '/api/secret?xhr=1'); x.withCredentials = true; x.onload = () => res(`${x.status} ${x.responseText}`); x.onerror = () => rej(new Error('xhr error')); x.send(); }));
  await t('WebSocket', () => new Promise((res, rej) => { const ws = new WebSocket(hostUrl.replace('http', 'ws') + '/ws/x'); ws.onopen = () => res('open'); ws.onerror = () => rej(new Error('ws error')); }));
  await t('image beacon', () => new Promise((res, rej) => { const i = new Image(); i.onload = () => res('loaded'); i.onerror = () => rej(new Error('img error')); i.src = hostUrl + '/api/secret?beacon=1'; }));
  await t('window.open', () => { const w = window.open(hostUrl + '/api/secret?popup=1'); return w ? 'opened' : (() => { throw new Error('returned null'); })(); });
  await t('form submit', () => { const f = document.createElement('form'); f.action = hostUrl + '/api/secret?form=1'; f.method = 'POST'; f.target = '_blank'; document.body.appendChild(f); f.submit(); return 'submitted (check server log)'; });
  return r;
}
