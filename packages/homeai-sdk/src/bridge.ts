// Sandbox side of the bridge (protocol: ./protocol.ts).
// Sandbox -> host: window.ReactNativeWebView.postMessage(str) in a WebView,
// otherwise parent.postMessage(str, '*') (the host checks event.source).
// Host -> sandbox: iframe.contentWindow.postMessage(str, '*') on web,
// webview.injectJavaScript(`__homeaiReceive(${str})`) on native.
import { PROTOCOL, parseEnvelope, type Envelope, type Method, type Methods } from './protocol';

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

export class BridgeError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = 'BridgeError';
  }
}

export function rpc<M extends Method>(method: M, params: Methods[M]['params'], timeoutMs = 15000): Promise<Methods[M]['result']> {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new BridgeError('timeout', `${method} got no answer from the host within ${timeoutMs / 1000} s`));
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
  return () => void set!.delete(fn);
}

function receive(raw: unknown) {
  const env = parseEnvelope(raw);
  if (!env) return;
  if (env.kind === 'res') {
    const p = pending.get(env.id);
    if (!p) return;
    pending.delete(env.id);
    clearTimeout(p.timer);
    if (env.ok) p.resolve(env.result);
    else p.reject(new BridgeError(String(env.error?.code ?? 'error'), String(env.error?.message ?? 'request failed')));
  } else if (env.kind === 'evt') {
    listeners.get(env.event)?.forEach((fn) => fn(env.data));
  }
}

export function installReceiver() {
  window.__homeaiReceive = receive;
  window.addEventListener('message', (e) => {
    // On web only the embedding host may talk to us. A WebView is top-level
    // (parent === window) and its host uses __homeaiReceive instead, so its
    // own MessageEvents (document on Android, window on iOS) are ignored.
    if (window.parent !== window && e.source === window.parent) receive(e.data);
  });
}
