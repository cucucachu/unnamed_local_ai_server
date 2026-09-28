// Host side of the bridge, the same for the web iframe and the native
// WebView (only `send` differs). Everything from the sandbox is untrusted:
// the envelope, method and params are checked here, only the known param
// fields are passed on, and `forward` is bound by the caller to one fixed
// instance — nothing in a message can name another.
import {
  HOST_METHODS,
  METHODS,
  PROTOCOL,
  WRITE_METHODS,
  parseEnvelope,
  type BridgeError,
  type Envelope,
  type HostEvents,
  type HostMethod,
  type Method,
  type Methods,
  type PlatformMethod,
  type SandboxEvents,
  type Space,
} from '../protocol';

export type Forward = <M extends PlatformMethod>(method: M, params: Methods[M]['params']) => Promise<Methods[M]['result']>;

export type BridgeHostOptions = {
  /** Delivers one wire string to the sandbox (postMessage on web, injectJavaScript on native). */
  send: (wire: string) => void;
  forward: Forward;
  /** Viewers: `db.run` and `action` are refused before reaching the platform (which refuses them too). */
  readOnly?: boolean;
  onEvent?: <E extends keyof SandboxEvents>(event: E, data: SandboxEvents[E]) => void;
  /** `agent.ask`: open the host's agent panel. Viewers may ask; writes still follow their role. */
  onAskAgent?: (prompt: string) => void | Promise<void>;
};

export type BridgeHost = {
  /** Feed every message the sandbox sent (the caller has checked it came from this sandbox). */
  receive(raw: unknown): void;
  event<E extends keyof HostEvents>(event: E, data: HostEvents[E]): void;
  dbChanged(): void;
  loadBundle(code: string): void;
  setSpace(space: Space): void;
  /** Stops replying and sending; in-flight requests are dropped. */
  close(): void;
};

export const MAX_IN_FLIGHT = 32;
const SANDBOX_EVENTS = new Set<string>(['runtime.ready', 'runtime.error', 'nav.changed']);
const ALLOWED_METHODS = new Set<string>([...METHODS, ...HOST_METHODS]);
const MAX_SQL_CHARS = 100 * 1024;
const MAX_PROMPT_CHARS = 32 * 1024;

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

function hostError(code: string, message: string): BridgeError & Error {
  return Object.assign(new Error(message), { code });
}

/** The params the platform (or host) will see, rebuilt from known fields only; throws `bad_request`. */
export function checkParams<M extends Method>(method: M, raw: unknown): Methods[M]['params'] {
  if (!isRecord(raw)) throw hostError('bad_request', `${method} needs an object of params`);
  if (method === 'agent.ask') {
    if (typeof raw.prompt !== 'string' || raw.prompt.length > MAX_PROMPT_CHARS) {
      throw hostError('bad_request', 'agent.ask needs a prompt (at most 32 KiB)');
    }
    return { prompt: raw.prompt } as Methods[M]['params'];
  }
  if (method === 'action') {
    if (typeof raw.name !== 'string' || !raw.name) throw hostError('bad_request', 'action needs a name');
    const params = raw.params ?? {};
    if (!isRecord(params)) throw hostError('bad_request', 'action params must be an object');
    return { name: raw.name, params } as Methods[M]['params'];
  }
  if (typeof raw.sql !== 'string' || raw.sql.length > MAX_SQL_CHARS) throw hostError('bad_request', `${method} needs sql (at most 100 KiB)`);
  const params = raw.params ?? [];
  if (!Array.isArray(params) && !isRecord(params)) throw hostError('bad_request', 'params must be a list or an object');
  return { sql: raw.sql, params } as Methods[M]['params'];
}

export function createBridgeHost({ send, forward, readOnly = false, onEvent, onAskAgent }: BridgeHostOptions): BridgeHost {
  let closed = false;
  let heard = false;
  let inFlight = 0;
  // Until the sandbox has spoken, its window may still be the initial
  // about:blank one, and a message sent to it now would be lost.
  const queued: string[] = [];

  const deliver = (env: Envelope) => {
    if (closed) return;
    const wire = JSON.stringify(env);
    if (heard) send(wire);
    else queued.push(wire);
  };
  const reply = (id: number, result: { ok: true; value: unknown } | { ok: false; error: BridgeError }) => {
    if (closed) return;
    send(JSON.stringify(result.ok ? { homeai: PROTOCOL, kind: 'res', id, ok: true, result: result.value } : { homeai: PROTOCOL, kind: 'res', id, ok: false, error: result.error }));
  };

  async function request(id: number, method: string, rawParams: unknown) {
    if (!ALLOWED_METHODS.has(method)) {
      return reply(id, { ok: false, error: { code: 'method_not_allowed', message: `the host doesn't provide ${method}` } });
    }
    const m = method as Method;
    if (readOnly && (WRITE_METHODS as readonly string[]).includes(m)) {
      return reply(id, { ok: false, error: { code: 'read_only', message: 'you can only view this app\'s data' } });
    }
    if (inFlight >= MAX_IN_FLIGHT) {
      return reply(id, { ok: false, error: { code: 'busy', message: `more than ${MAX_IN_FLIGHT} requests at once` } });
    }
    inFlight++;
    try {
      if ((HOST_METHODS as readonly string[]).includes(m)) {
        const params = checkParams(m as HostMethod, rawParams) as Methods['agent.ask']['params'];
        await onAskAgent?.(params.prompt);
        reply(id, { ok: true, value: {} });
        return;
      }
      const value = await forward(m as PlatformMethod, checkParams(m as PlatformMethod, rawParams));
      reply(id, { ok: true, value });
    } catch (err: any) {
      reply(id, { ok: false, error: { code: typeof err?.code === 'string' ? err.code : 'host_error', message: String(err?.message ?? err) } });
    } finally {
      inFlight--;
    }
  }

  return {
    receive(raw) {
      if (closed) return;
      const env = parseEnvelope(raw);
      if (!env) return;
      if (!heard) {
        heard = true;
        queued.splice(0).forEach(send);
      }
      if (env.kind === 'req') void request(env.id, env.method, env.params);
      else if (env.kind === 'evt' && SANDBOX_EVENTS.has(env.event)) onEvent?.(env.event as keyof SandboxEvents, env.data as any);
    },
    event: (event, data) => deliver({ homeai: PROTOCOL, kind: 'evt', event, data }),
    dbChanged() {
      this.event('db.changed', {});
    },
    loadBundle(code) {
      this.event('bundle.load', { code });
    },
    setSpace(space) {
      this.event('space', space);
    },
    close() {
      closed = true;
      queued.length = 0;
    },
  };
}
