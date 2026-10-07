// Typings app code is checked against for the modules the runtime provides
// besides react / react-native (docs/PLATFORM.md §7 "Allowed imports").
// They describe exactly what src/runtime.tsx registers: anything missing
// here is a type error in the app, which is how unsupported expo-router or
// SDK features are reported.

declare module '@homeai/sdk' {
  export type SQLValue = string | number | boolean | null;
  /** `?` placeholders take a list; `:name` / `$name` / `@name` take an object (the prefix is optional in its keys). */
  export type SQLParams = SQLValue[] | Record<string, SQLValue>;
  export type RunResult = { changes: number; lastInsertRowId: number };
  export type ActionResult = RunResult & { rows: Record<string, unknown>[] };

  /**
   * expo-sqlite-shaped access to this app instance's database. Params go as
   * a list, an object, or one by one: `runAsync(sql, [a, b])`,
   * `runAsync(sql, { $a: a })`, `runAsync(sql, a, b)`. One statement per call;
   * multi-statement writes are actions (`runAction`).
   */
  export interface Database {
    getAllAsync<T = any>(sql: string, params?: SQLParams): Promise<T[]>;
    getAllAsync<T = any>(sql: string, ...params: SQLValue[]): Promise<T[]>;
    getFirstAsync<T = any>(sql: string, params?: SQLParams): Promise<T | null>;
    getFirstAsync<T = any>(sql: string, ...params: SQLValue[]): Promise<T | null>;
    runAsync(sql: string, params?: SQLParams): Promise<RunResult>;
    runAsync(sql: string, ...params: SQLValue[]): Promise<RunResult>;
  }

  /** What SDK calls reject with: `code` is the platform's (`sql_error`, `sql_not_allowed`, …) or the host's (`read_only`, `timeout`, …). */
  export type SDKError = Error & { code: string };

  export type QueryResult<T> = {
    data: T[] | undefined;
    error: Error | null;
    loading: boolean;
    refresh: () => void;
  };

  export type Space = { id: string; slug: string; name: string; role: 'owner' | 'editor' | 'viewer' };
  /** A person. `id` is what the platform stamps in every table's `_created_by` / `_updated_by`. */
  export type User = { id: string; username: string; name: string };
  export type Member = User & { role: 'owner' | 'editor' | 'viewer' };

  export function useDatabase(): Database;
  /** Alias of `useDatabase` (expo-sqlite's hook name). */
  export function useSQLiteContext(): Database;
  /** Runs `sql` and re-runs it whenever the instance's database changes. */
  export function useQuery<T = any>(sql: string, params?: SQLParams): QueryResult<T>;
  export function useQuery<T = any>(sql: string, ...params: SQLValue[]): QueryResult<T>;
  /** Runs the named action from actions/<name>.sql in one transaction; `rows` are the last result set's. */
  export function runAction(name: string, params?: Record<string, SQLValue>): Promise<ActionResult>;
  /** Opens the host's "Ask the agent" panel with this prompt (a new thread pre-seeded with this app's context). */
  export function askAgent(prompt: string): Promise<void>;
  /** The space this instance is installed in; null until the host has said. */
  export function useSpace(): Space | null;
  /** Who is using the app; null until the host has said. */
  export function useUser(): User | null;
  /** The space's members, to show names for `_created_by` / `_updated_by`. */
  export function useMembers(): Member[];
  /** The member with this user id (e.g. `useMember(row._created_by)`), or undefined. */
  export function useMember(id: string | null | undefined): Member | undefined;
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
