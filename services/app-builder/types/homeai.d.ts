// Typings app code is checked against for the modules the runtime provides
// besides react / react-native (docs/PLATFORM.md §7 "Allowed imports").
// They describe exactly what runtime/index.tsx registers: anything missing
// here is a type error in the app, which is how unsupported expo-router or
// SDK features are reported.

declare module '@homeai/sdk' {
  export type SQLParams = unknown[];
  export type RunResult = { changes: number; lastInsertRowId: number };

  /** expo-sqlite-shaped access to this app instance's database. */
  export interface Database {
    getAllAsync<T = any>(sql: string, params?: SQLParams): Promise<T[]>;
    getFirstAsync<T = any>(sql: string, params?: SQLParams): Promise<T | null>;
    runAsync(sql: string, params?: SQLParams): Promise<RunResult>;
  }

  export type QueryResult<T> = {
    data: T[] | undefined;
    error: Error | null;
    loading: boolean;
    refresh: () => void;
  };

  export type Space = { id: string; slug: string; name: string; role: 'owner' | 'editor' | 'viewer' };

  export function useDatabase(): Database;
  /** Runs `sql` and re-runs it whenever the instance's database changes. */
  export function useQuery<T = any>(sql: string, params?: SQLParams): QueryResult<T>;
  /** Runs the named action from actions/<name>.sql in one transaction. */
  export function runAction(name: string, params?: Record<string, unknown>): Promise<unknown>;
  /** The space this instance is installed in; null until the host has said. */
  export function useSpace(): Space | null;
}

declare module 'expo-sqlite' {
  import type { ReactNode } from 'react';
  import type { Database } from '@homeai/sdk';

  export type SQLiteDatabase = Database;
  export function useSQLiteContext(): SQLiteDatabase;
  export function openDatabaseAsync(name?: string): Promise<SQLiteDatabase>;
  export function SQLiteProvider(props: { databaseName?: string; children?: ReactNode }): ReactNode;
}

declare module 'expo-router' {
  import type { ReactElement, ReactNode } from 'react';

  export type Href = string | { pathname: string; params?: Record<string, unknown> };
  export type ScreenOptions = { title?: string; headerShown?: boolean };
  type Params = Record<string, string | string[]>;

  export interface Router {
    push(href: Href): void;
    navigate(href: Href): void;
    replace(href: Href): void;
    back(): void;
    canGoBack(): boolean;
    dismissAll(): void;
    setParams(params: Params): void;
  }

  export const router: Router;
  export function useRouter(): Router;
  export function useLocalSearchParams<T extends Params = Params>(): T;
  export function useGlobalSearchParams<T extends Params = Params>(): T;
  export function usePathname(): string;

  export function Stack(props: { children?: ReactNode; screenOptions?: ScreenOptions }): ReactElement;
  export namespace Stack {
    function Screen(props: { name?: string; options?: ScreenOptions }): ReactElement | null;
  }
  export function Slot(): ReactElement | null;
  export function Link(props: {
    href: Href;
    replace?: boolean;
    asChild?: boolean;
    children?: ReactNode;
    style?: unknown;
    testID?: string;
  }): ReactElement;
}
