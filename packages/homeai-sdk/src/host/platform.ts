// The host's side of the platform contract (docs/ARCHITECTURE.md §3 "App
// data"): bridge methods -> `POST /api/platform/apps/instances/{id}/rpc`, the
// instance's bundle and the runtime, and `/ws/platform/events` -> sandbox
// events. The host's own credentials go on these requests (the web session
// cookie, or a bearer on native); the sandbox never sees them.
import type { Forward } from './bridge-host';
import type { BridgeHost } from './bridge-host';
import type { PlatformMethod, Methods } from '../protocol';

export type FetchLike = (
  url: string,
  init?: { method?: string; headers?: Record<string, string>; body?: string; credentials?: 'same-origin' | 'include' | 'omit' },
) => Promise<{ ok: boolean; status: number; json(): Promise<any>; text(): Promise<string> }>;

export type PlatformOptions = {
  /** '' on the web host (same origin); `http://homeai.local` etc. on native. */
  baseUrl?: string;
  fetch?: FetchLike;
  /** e.g. `{ Authorization: 'Bearer …' }` on native. */
  headers?: Record<string, string>;
};

export type Bundle = { app_id: string; version: string; sdk: string; bundle_id: string; code: string };

const OPS: Record<PlatformMethod, string> = { 'db.getAll': 'getAll', 'db.getFirst': 'getFirst', 'db.run': 'run', action: 'action' };

function withError(code: string, message: string) {
  return Object.assign(new Error(message), { code });
}

async function call(opts: PlatformOptions, path: string, body?: unknown) {
  const doFetch = opts.fetch ?? (globalThis as unknown as { fetch: FetchLike }).fetch;
  let r: Awaited<ReturnType<FetchLike>>;
  try {
    r = await doFetch(`${opts.baseUrl ?? ''}${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...opts.headers },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'same-origin',
    });
  } catch (err: any) {
    throw withError('unavailable', `the platform is unreachable: ${err?.message ?? err}`);
  }
  let doc: any = null;
  try {
    doc = await r.json();
  } catch {
    // An empty or non-JSON error body: fall through to the status.
  }
  if (!r.ok) {
    const code = typeof doc?.detail === 'string' ? doc.detail : `http_${r.status}`;
    throw withError(code, typeof doc?.message === 'string' ? doc.message : code);
  }
  return doc;
}

const instancePath = (instanceId: string) => `/api/platform/apps/instances/${encodeURIComponent(instanceId)}`;

/** A `forward` for createBridgeHost bound to one instance. */
export function platformForward(instanceId: string, opts: PlatformOptions = {}): Forward {
  const url = `${instancePath(instanceId)}/rpc`;
  return (async (method: PlatformMethod, params: Methods[PlatformMethod]['params']) => {
    const op = OPS[method];
    const doc = await call(opts, url, { op, ...params });
    if (method === 'db.getAll') return doc.rows;
    if (method === 'db.getFirst') return doc.row;
    if (method === 'db.run') return { changes: doc.changes, lastInsertRowId: doc.lastInsertRowId };
    return { changes: doc.changes, lastInsertRowId: doc.lastInsertRowId, rows: doc.rows };
  }) as Forward;
}

export function fetchBundle(instanceId: string, opts: PlatformOptions = {}): Promise<Bundle> {
  return call(opts, `${instancePath(instanceId)}/bundle`);
}

/** Where the platform serves the runtime for SDK `sdk` (static, public: it holds no data). */
export function runtimeUrl(sdk: string, baseUrl = '') {
  return `${baseUrl}/app-runtime/${encodeURIComponent(sdk)}/runtime.js`;
}

export async function fetchRuntime(sdk: string, opts: PlatformOptions = {}): Promise<string> {
  const doFetch = opts.fetch ?? (globalThis as unknown as { fetch: FetchLike }).fetch;
  const r = await doFetch(runtimeUrl(sdk, opts.baseUrl));
  if (!r.ok) throw withError(`http_${r.status}`, `no runtime for SDK ${sdk}`);
  return r.text();
}

/**
 * Handler for `/ws/platform/events` frames (the caller owns the socket):
 * `ready` (a (re)connect, after which nothing is replayed) and this
 * instance's `db_changed` become `db.changed`; this app's `app_built`
 * fetches the new bundle and hot-reloads it.
 */
export function platformEventRelay({
  instanceId,
  appId,
  host,
  reload,
  onError,
}: {
  instanceId: string;
  appId: string;
  host: BridgeHost;
  reload?: () => Promise<string>;
  onError?: (err: unknown) => void;
}) {
  let reloading: Promise<void> | null = null;
  let again = false;
  const hotReload = () => {
    if (!reload) return;
    if (reloading) {
      again = true;
      return;
    }
    reloading = reload()
      .then((code) => host.loadBundle(code), onError)
      .finally(() => {
        reloading = null;
        if (again) {
          again = false;
          hotReload();
        }
      });
  };
  return (frame: unknown) => {
    let msg: any = frame;
    if (typeof frame === 'string') {
      try {
        msg = JSON.parse(frame);
      } catch {
        return;
      }
    }
    if (msg?.type === 'ready') host.dbChanged();
    else if (msg?.type === 'db_changed' && msg.instance_id === instanceId) host.dbChanged();
    else if (msg?.type === 'app_built' && msg.app_id === appId) hotReload();
  };
}
