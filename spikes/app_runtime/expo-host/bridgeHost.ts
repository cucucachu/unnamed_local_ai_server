// Host side of the bridge, shared by the iframe (web) and WebView (native)
// transports. Messages from the sandbox are untrusted: parse, check the
// envelope and method allowlist, then forward to a fixed instance.
export type Envelope = { homeai: 1; kind: 'req' | 'res' | 'evt'; id?: number; method?: string; params?: any; ok?: boolean; result?: any; error?: any; event?: string; data?: any };

export const INSTANCE = 'inst-123';
const METHODS = new Set(['echo', 'db.getAll', 'db.getFirst', 'db.run', 'action']);

/** The script the native host passes to WebView.injectJavaScript to deliver one envelope. */
export function injectScriptFor(env: Envelope) {
  return `window.__homeaiReceive && window.__homeaiReceive(${JSON.stringify(JSON.stringify(env))});true;`;
}

export type RpcHandler = (method: string, params: any) => Promise<any>;

export function makeHost(send: (env: Envelope) => void, rpc: RpcHandler, onEvent: (event: string, data: any) => void) {
  const reply = (id: number, ok: boolean, payload: any) =>
    send(ok ? { homeai: 1, kind: 'res', id, ok: true, result: payload } : { homeai: 1, kind: 'res', id, ok: false, error: payload });

  async function receive(raw: unknown) {
    let env: Envelope;
    try {
      env = typeof raw === 'string' ? JSON.parse(raw) : (raw as Envelope);
    } catch {
      return;
    }
    if (!env || env.homeai !== 1) return;
    if (env.kind === 'evt' && env.event) return onEvent(env.event, env.data);
    if (env.kind !== 'req' || typeof env.id !== 'number' || !env.method) return;
    if (!METHODS.has(env.method)) return reply(env.id, false, { code: 'method-not-allowed', message: env.method });
    if (env.method === 'echo') return reply(env.id, true, env.params);
    try {
      const result = await rpc(env.method, env.params);
      reply(env.id, true, result);
      if (env.method === 'db.run') send({ homeai: 1, kind: 'evt', event: 'db.changed', data: { instance: INSTANCE } });
    } catch (err: any) {
      reply(env.id, false, { code: err?.code ?? 'rpc', message: String(err?.message ?? err) });
    }
  }

  return {
    receive,
    event: (event: string, data?: any) => send({ homeai: 1, kind: 'evt', event, data }),
  };
}

export function stats(samples: number[]) {
  const s = [...samples].sort((a, b) => a - b);
  const q = (p: number) => s[Math.min(s.length - 1, Math.floor(p * s.length))];
  return { n: s.length, p50: +q(0.5).toFixed(2), p95: +q(0.95).toFixed(2), max: +s[s.length - 1].toFixed(2) };
}
