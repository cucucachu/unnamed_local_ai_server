// Sandbox side of the host <-> sandbox bridge.
//
// Envelope (JSON string on the wire, identical for iframe and WebView):
//   { homeai: 1, kind: 'req', id, method, params }
//   { homeai: 1, kind: 'res', id, ok: true, result } | { ..., ok: false, error: { code, message } }
//   { homeai: 1, kind: 'evt', event, data }
// Sandbox -> host: window.ReactNativeWebView.postMessage(str) in a WebView,
// otherwise parent.postMessage(str, '*') (the host checks event.source).
// Host -> sandbox: iframe.contentWindow.postMessage(str, '*') on web,
// webview.injectJavaScript(`__homeaiReceive(${str})`) on native.

export const PROTOCOL = 1;

export type Envelope =
  | { homeai: 1; kind: 'req'; id: number; method: string; params?: unknown }
  | { homeai: 1; kind: 'res'; id: number; ok: true; result: unknown }
  | { homeai: 1; kind: 'res'; id: number; ok: false; error: { code: string; message: string } }
  | { homeai: 1; kind: 'evt'; event: string; data?: unknown };

type Pending = { resolve: (v: unknown) => void; reject: (e: Error) => void; timer: ReturnType<typeof setTimeout> };

const pending = new Map<number, Pending>();
const listeners = new Map<string, Set<(data: any) => void>>();
let nextId = 1;

declare global {
  interface Window {
    ReactNativeWebView?: { postMessage(msg: string): void };
    __homeaiReceive?: (msg: unknown) => void;
  }
}

function send(env: Envelope) {
  const wire = JSON.stringify(env);
  if (window.ReactNativeWebView) window.ReactNativeWebView.postMessage(wire);
  else window.parent.postMessage(wire, '*');
}

export function rpc<T = unknown>(method: string, params?: unknown, timeoutMs = 15000): Promise<T> {
  const id = nextId++;
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(Object.assign(new Error(`RPC ${method} timed out`), { code: 'timeout' }));
    }, timeoutMs);
    pending.set(id, { resolve: resolve as (v: unknown) => void, reject, timer });
    send({ homeai: PROTOCOL, kind: 'req', id, method, params });
  });
}

export function emit(event: string, data?: unknown) {
  send({ homeai: PROTOCOL, kind: 'evt', event, data });
}

export function on(event: string, fn: (data: any) => void): () => void {
  let set = listeners.get(event);
  if (!set) listeners.set(event, (set = new Set()));
  set.add(fn);
  return () => set!.delete(fn);
}

function receive(raw: unknown) {
  let env: Envelope;
  try {
    env = typeof raw === 'string' ? JSON.parse(raw) : (raw as Envelope);
  } catch {
    return;
  }
  if (!env || env.homeai !== PROTOCOL) return;
  if (env.kind === 'res') {
    const p = pending.get(env.id);
    if (!p) return;
    pending.delete(env.id);
    clearTimeout(p.timer);
    if (env.ok) p.resolve(env.result);
    else p.reject(Object.assign(new Error(env.error.message), { code: env.error.code }));
  } else if (env.kind === 'evt') {
    listeners.get(env.event)?.forEach((fn) => fn(env.data));
  }
}

export function installReceiver() {
  window.__homeaiReceive = receive;
  window.addEventListener('message', (e) => {
    // On web only the embedding host may talk to us; in a WebView the host
    // uses injectJavaScript -> __homeaiReceive instead.
    if (window.parent !== window && e.source === window.parent) receive(e.data);
  });
}
