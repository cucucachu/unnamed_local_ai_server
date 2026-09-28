// Bridge protocol v1, shared by the sandbox (src/bridge.ts) and the host
// (src/host). Contract: docs/PLATFORM.md §7 "Bridge protocol".
//
// Every message is a JSON string on the wire (a WebView carries only
// strings):
//   { homeai: 1, kind: 'req', id, method, params }
//   { homeai: 1, kind: 'res', id, ok: true, result } | { ..., ok: false, error: { code, message } }
//   { homeai: 1, kind: 'evt', event, data }
// No DOM or React Native types here: both hosts import this.

export const PROTOCOL = 1;

export type BridgeError = { code: string; message: string };

export type Envelope =
  | { homeai: 1; kind: 'req'; id: number; method: string; params?: unknown }
  | { homeai: 1; kind: 'res'; id: number; ok: true; result: unknown }
  | { homeai: 1; kind: 'res'; id: number; ok: false; error: BridgeError }
  | { homeai: 1; kind: 'evt'; event: string; data?: unknown };

export type SqlParams = unknown[] | Record<string, unknown>;
export type RunResult = { changes: number; lastInsertRowId: number };
export type ActionResult = RunResult & { rows: Record<string, unknown>[] };
export type Space = { id: string; slug: string; name: string; role: 'owner' | 'editor' | 'viewer' };

/** Sandbox -> host requests that the host forwards to the instance RPC. */
export type PlatformMethods = {
  'db.getAll': { params: { sql: string; params: SqlParams }; result: Record<string, unknown>[] };
  'db.getFirst': { params: { sql: string; params: SqlParams }; result: Record<string, unknown> | null };
  'db.run': { params: { sql: string; params: SqlParams }; result: RunResult };
  action: { params: { name: string; params: Record<string, unknown> }; result: ActionResult };
};
/** Sandbox -> host requests the host handles itself (never the platform). */
export type HostMethods = {
  /** Open the host's agent panel with this prompt (M13-04). */
  'agent.ask': { params: { prompt: string }; result: Record<string, never> };
};
export type Methods = PlatformMethods & HostMethods;
export type PlatformMethod = keyof PlatformMethods;
export type HostMethod = keyof HostMethods;
export type Method = keyof Methods;

export const METHODS: readonly PlatformMethod[] = ['db.getAll', 'db.getFirst', 'db.run', 'action'];
export const HOST_METHODS: readonly HostMethod[] = ['agent.ask'];
export const WRITE_METHODS: readonly PlatformMethod[] = ['db.run', 'action'];

/** Host -> sandbox events. */
export type HostEvents = {
  'db.changed': Record<string, never>;
  'bundle.load': { code: string };
  space: Space;
};

/** Sandbox -> host events. */
export type SandboxEvents = {
  'runtime.ready': { version: number; renderMs: number };
  'runtime.error': { message: string; stack: string; componentStack: string };
  'nav.changed': { path: string };
};

/** What the host puts in the sandbox document (`window.__homeai_config`). */
export type SandboxConfig = { initialPath?: string; space?: Space | null };

/** The largest envelope either side accepts, in UTF-16 code units. */
export const MAX_MESSAGE_CHARS = 1 << 20;

/** Parses a wire message; null for anything that isn't a v1 envelope. */
export function parseEnvelope(raw: unknown): Envelope | null {
  if (typeof raw !== 'string' || raw.length > MAX_MESSAGE_CHARS) return null;
  let env: any;
  try {
    env = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!env || typeof env !== 'object' || env.homeai !== PROTOCOL) return null;
  if (env.kind === 'req') return Number.isSafeInteger(env.id) && typeof env.method === 'string' ? env : null;
  if (env.kind === 'res') return Number.isSafeInteger(env.id) && typeof env.ok === 'boolean' ? env : null;
  if (env.kind === 'evt') return typeof env.event === 'string' ? env : null;
  return null;
}
