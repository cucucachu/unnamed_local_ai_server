// @homeai/sdk (SDK 1) + the expo-sqlite shim. Every call is a bridge RPC;
// the sandbox never talks to the network itself. App-facing typings:
// types/homeai.d.ts.
import { createElement, Fragment, useCallback, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { on, rpc } from './bridge';
import type { ActionResult, RunResult, SqlParams, Space } from './protocol';

export type Database = {
  getAllAsync<T = any>(sql: string, ...params: unknown[]): Promise<T[]>;
  getFirstAsync<T = any>(sql: string, ...params: unknown[]): Promise<T | null>;
  runAsync(sql: string, ...params: unknown[]): Promise<RunResult>;
  withTransactionAsync(fn: () => Promise<void>): Promise<void>;
};

const changeListeners = new Set<() => void>();
const changed = () => changeListeners.forEach((fn) => fn());
on('db.changed', changed);

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v) && Object.getPrototypeOf(v) === Object.prototype;

/** expo-sqlite's bind forms: `(sql, [a, b])`, `(sql, { $x: 1 })`, or `(sql, a, b)`. */
export function bindParams(args: unknown[]): SqlParams {
  if (args.length === 1 && (Array.isArray(args[0]) || isRecord(args[0]))) return args[0] as SqlParams;
  if (args.length === 1 && args[0] === undefined) return [];
  return args;
}

export const database: Database = {
  getAllAsync: (sql, ...params) => rpc('db.getAll', { sql, params: bindParams(params) }) as Promise<any[]>,
  getFirstAsync: (sql, ...params) => rpc('db.getFirst', { sql, params: bindParams(params) }) as Promise<any>,
  async runAsync(sql, ...params) {
    const r = await rpc('db.run', { sql, params: bindParams(params) });
    // The platform's db_changed comes back through the host too; this shows
    // our own write without waiting for that round trip.
    if (r.changes) changed();
    return r;
  },
  async withTransactionAsync() {
    throw new Error('withTransactionAsync is not available in SDK 1: put multi-statement writes in an action and call runAction');
  },
};

export function useDatabase(): Database {
  return database;
}

type QueryState<T> = { data: T[] | undefined; error: Error | null; loading: boolean };

export function useQuery<T = any>(sql: string, ...params: unknown[]) {
  const key = JSON.stringify([sql, bindParams(params)]);
  const [state, setState] = useState<QueryState<T> & { key: string }>({ key, data: undefined, error: null, loading: true });
  // One query in flight per hook; changes that arrive meanwhile collapse into one re-run.
  const run = useRef({ key, busy: false, again: false });
  const refresh = useCallback(() => {
    const r = run.current;
    if (r.key !== key) return;
    if (r.busy) {
      r.again = true;
      return;
    }
    r.busy = true;
    const [q, p] = JSON.parse(key) as [string, SqlParams];
    rpc('db.getAll', { sql: q, params: p }).then(
      (data) => run.current === r && setState({ key, data: data as T[], error: null, loading: false }),
      (error) => run.current === r && setState((s) => ({ ...s, key, error, loading: false })),
    ).finally(() => {
      r.busy = false;
      if (r.again) {
        r.again = false;
        refresh();
      }
    });
  }, [key]);
  useEffect(() => {
    run.current = { key, busy: false, again: false };
    refresh();
    changeListeners.add(refresh);
    return () => void changeListeners.delete(refresh);
  }, [key, refresh]);
  const current = state.key === key ? state : { data: undefined, error: null, loading: true };
  return { data: current.data, error: current.error, loading: current.loading, refresh };
}

export async function runAction(name: string, params: Record<string, unknown> = {}): Promise<ActionResult> {
  const r = await rpc('action', { name, params });
  if (r.changes) changed();
  return r;
}

let space: Space | null = null;
const spaceListeners = new Set<() => void>();
export function setSpace(next: Space | null) {
  space = next;
  spaceListeners.forEach((fn) => fn());
}
on('space', (s: Space) => setSpace(s));

export function useSpace(): Space | null {
  return useSyncExternalStore(
    (fn) => (spaceListeners.add(fn), () => void spaceListeners.delete(fn)),
    () => space,
    () => space,
  );
}

export const expoSqlite = {
  useSQLiteContext: () => database,
  openDatabaseAsync: async () => database,
  SQLiteProvider: ({ children }: { children?: ReactNode }) => createElement(Fragment, null, children),
};
