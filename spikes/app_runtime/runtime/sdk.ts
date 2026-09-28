// @homeai/sdk stub + expo-sqlite shim. Every call is an RPC; the sandbox
// never talks to the network itself.
import { useCallback, useEffect, useRef, useState } from 'react';
import { on, rpc } from './bridge';

export type RunResult = { changes: number; lastInsertRowId: number };

export type Database = {
  getAllAsync<T = any>(sql: string, params?: unknown[]): Promise<T[]>;
  getFirstAsync<T = any>(sql: string, params?: unknown[]): Promise<T | null>;
  runAsync(sql: string, params?: unknown[]): Promise<RunResult>;
  withTransactionAsync(fn: () => Promise<void>): Promise<void>;
};

const changeListeners = new Set<() => void>();
on('db.changed', () => changeListeners.forEach((fn) => fn()));

export const database: Database = {
  getAllAsync: (sql, params = []) => rpc('db.getAll', { sql, params }),
  getFirstAsync: (sql, params = []) => rpc('db.getFirst', { sql, params }),
  async runAsync(sql, params = []) {
    const r = await rpc<RunResult>('db.run', { sql, params });
    // The host also relays platform change events; this makes our own writes
    // visible immediately without waiting for the round trip through the ws.
    changeListeners.forEach((fn) => fn());
    return r;
  },
  async withTransactionAsync() {
    throw new Error('withTransactionAsync: not implemented in the M12-01 spike (needs a server-side tx lease)');
  },
};

export function useDatabase(): Database {
  return database;
}

export function useQuery<T = any>(sql: string, params: unknown[] = []) {
  const [state, setState] = useState<{ data: T[] | undefined; error: Error | null; loading: boolean }>({
    data: undefined,
    error: null,
    loading: true,
  });
  const key = JSON.stringify([sql, params]);
  const seq = useRef(0);
  const refresh = useCallback(() => {
    const mine = ++seq.current;
    database.getAllAsync<T>(sql, params).then(
      (data) => mine === seq.current && setState({ data, error: null, loading: false }),
      (error) => mine === seq.current && setState((s) => ({ ...s, error, loading: false })),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  useEffect(() => {
    refresh();
    changeListeners.add(refresh);
    return () => void changeListeners.delete(refresh);
  }, [refresh]);
  return { ...state, refresh };
}

export function runAction(name: string, params: Record<string, unknown> = {}) {
  return rpc('action', { name, params });
}

let space: { id: string; slug: string; name: string; role: string } | null = null;
on('space', (s) => (space = s));
export function useSpace() {
  return space;
}

export const expoSqlite = {
  useSQLiteContext: () => database,
  openDatabaseAsync: async () => database,
  SQLiteProvider: ({ children }: { children: any }) => children,
};
