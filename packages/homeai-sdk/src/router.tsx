// Minimal expo-router-compatible shim: Stack (+ Stack.Screen), Slot, Link,
// router / useRouter, useLocalSearchParams, useGlobalSearchParams,
// usePathname. The route table is generated at build time from app/**.
import {
  Children,
  cloneElement,
  createContext,
  isValidElement,
  useContext,
  useEffect,
  useState,
  useSyncExternalStore,
  type ComponentType,
  type ReactElement,
  type ReactNode,
} from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

export type RouteDef = { name: string; segments: string[]; component: ComponentType<any> };
export type RouteTable = { layout?: ComponentType<any>; routes: RouteDef[] };
type Params = Record<string, string | string[]>;
type Entry = { key: number; name: string | null; path: string; params: Params };
export type Href = string | { pathname: string; params?: Record<string, unknown> };
type ScreenOptions = { title?: string; headerShown?: boolean };

let table: RouteTable = { routes: [] };
let stack: Entry[] = [];
let nextKey = 1;
const subs = new Set<() => void>();
let onChange: (path: string) => void = () => {};

function specificity(r: RouteDef) {
  return r.segments.reduce((n, s) => n + (s.startsWith('[...') ? 100 : s.startsWith('[') ? 10 : 1), 0);
}

function hrefToPath(href: Href): string {
  if (typeof href === 'string') return href;
  const params = { ...(href.params ?? {}) } as Record<string, unknown>;
  let path = href.pathname.replace(/\[(\.\.\.)?([^\]]+)\]/g, (_, rest, name) => {
    const v = params[name];
    delete params[name];
    return rest && Array.isArray(v) ? v.map(encodeURIComponent).join('/') : encodeURIComponent(String(v ?? ''));
  });
  const qs = Object.entries(params)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join('&');
  if (qs) path += `?${qs}`;
  return path;
}

function resolve(path: string): Entry {
  const [pathname, query = ''] = path.split('?');
  const parts = pathname.split('/').filter(Boolean).map(decodeURIComponent);
  const params: Params = {};
  new URLSearchParams(query).forEach((v, k) => (params[k] = v));
  const sorted = [...table.routes].sort((a, b) => specificity(a) - specificity(b));
  for (const r of sorted) {
    const got: Params = {};
    let ok = true;
    for (let i = 0; i < r.segments.length && ok; i++) {
      const seg = r.segments[i];
      if (seg.startsWith('[...')) {
        got[seg.slice(4, -1)] = parts.slice(i);
        ok = parts.length > i;
        i = parts.length;
        break;
      } else if (seg.startsWith('[')) {
        if (i >= parts.length) ok = false;
        else got[seg.slice(1, -1)] = parts[i];
      } else if (seg !== parts[i]) ok = false;
    }
    const catchAll = r.segments.some((s) => s.startsWith('[...'));
    if (ok && (catchAll || r.segments.length === parts.length)) {
      return { key: nextKey++, name: r.name, path, params: { ...params, ...got } };
    }
  }
  return { key: nextKey++, name: null, path, params };
}

function commit(next: Entry[]) {
  stack = next;
  subs.forEach((fn) => fn());
  onChange(stack[stack.length - 1]?.path ?? '/');
}

export const router = {
  push: (href: Href) => commit([...stack, resolve(hrefToPath(href))]),
  navigate: (href: Href) => {
    const path = hrefToPath(href);
    const i = stack.findIndex((e) => e.path === path);
    commit(i >= 0 ? stack.slice(0, i + 1) : [...stack, resolve(path)]);
  },
  replace: (href: Href) => commit([...stack.slice(0, -1), resolve(hrefToPath(href))]),
  back: () => stack.length > 1 && commit(stack.slice(0, -1)),
  canGoBack: () => stack.length > 1,
  dismissAll: () => commit(stack.slice(0, 1)),
  setParams: (p: Params) => {
    const top = stack[stack.length - 1];
    commit([...stack.slice(0, -1), { ...top, params: { ...top.params, ...p } }]);
  },
};

/** Called by the runtime on boot and on every hot reload. Keeps the current path stack. */
export function setRouteTable(next: RouteTable, initialPath: string, notify: (path: string) => void) {
  table = next;
  onChange = notify;
  const paths = stack.length ? stack.map((e) => e.path) : [initialPath];
  commit(paths.map(resolve));
}

function useStack() {
  return useSyncExternalStore(
    (fn) => (subs.add(fn), () => void subs.delete(fn)),
    () => stack,
    () => stack,
  );
}

const EntryContext = createContext<{ entry: Entry; setOptions: (o: ScreenOptions) => void } | null>(null);

export function useRouter() {
  return router;
}
export function useLocalSearchParams<T = Params>(): T {
  return (useContext(EntryContext)?.entry.params ?? {}) as T;
}
export function useGlobalSearchParams<T = Params>(): T {
  const s = useStack();
  return (s[s.length - 1]?.params ?? {}) as T;
}
export function usePathname(): string {
  const s = useStack();
  return (s[s.length - 1]?.path ?? '/').split('?')[0];
}

function NotFound({ path }: { path: string }) {
  return (
    <View style={{ padding: 16 }}>
      <Text>Unmatched route: {path}</Text>
    </View>
  );
}

function ScreenHost({ entry, visible, base, depth, header }: { entry: Entry; visible: boolean; base: ScreenOptions; depth: number; header: boolean }) {
  const [own, setOwn] = useState<ScreenOptions>({});
  const opts = { ...base, ...own };
  const route = table.routes.find((r) => r.name === entry.name);
  const C = route?.component;
  return (
    <EntryContext.Provider value={{ entry, setOptions: setOwn }}>
      <View style={[styles.screen, !visible && styles.hidden]}>
        {header && opts.headerShown !== false && (
          <View style={styles.header}>
            {depth > 0 && (
              <Pressable testID="header-back" onPress={router.back} style={styles.back}>
                <Text style={styles.backText}>‹ Back</Text>
              </Pressable>
            )}
            <Text role="heading" style={styles.title}>
              {opts.title ?? entry.name ?? ''}
            </Text>
          </View>
        )}
        {C ? <C /> : <NotFound path={entry.path} />}
      </View>
    </EntryContext.Provider>
  );
}

function optionsFrom(children: ReactNode) {
  const out: Record<string, ScreenOptions> = {};
  Children.forEach(children, (c) => {
    if (isValidElement(c) && c.type === StackScreen) {
      const p = c.props as { name: string; options?: ScreenOptions };
      out[p.name] = p.options ?? {};
    }
  });
  return out;
}

export function Stack({ children, screenOptions = {} }: { children?: ReactNode; screenOptions?: ScreenOptions }) {
  const s = useStack();
  const byName = optionsFrom(children);
  return (
    <View style={styles.screen}>
      {s.map((e, i) => (
        <ScreenHost
          key={e.key}
          entry={e}
          depth={i}
          visible={i === s.length - 1}
          header
          base={{ ...screenOptions, ...(e.name ? byName[e.name] : {}) }}
        />
      ))}
    </View>
  );
}

function StackScreen({ options }: { name?: string; options?: ScreenOptions }) {
  const ctx = useContext(EntryContext);
  const key = JSON.stringify(options ?? {});
  useEffect(() => {
    if (ctx && options) ctx.setOptions(options);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return null;
}
Stack.Screen = StackScreen;

export function Slot() {
  const s = useStack();
  const top = s[s.length - 1];
  return top ? <ScreenHost entry={top} visible depth={0} header={false} base={{}} /> : null;
}

export function Link({ href, replace, asChild, children, ...rest }: { href: Href; replace?: boolean; asChild?: boolean; children?: ReactNode; [k: string]: unknown }) {
  const onPress = (e?: { preventDefault?: () => void }) => {
    e?.preventDefault?.();
    replace ? router.replace(href) : router.push(href);
  };
  if (asChild && isValidElement(children)) return cloneElement(children as ReactElement<any>, { onPress });
  return (
    <Text role="link" {...rest} onPress={onPress}>
      {children}
    </Text>
  );
}

/** Default root when the app has no app/_layout.tsx (expo-router renders a Stack). */
export function DefaultLayout() {
  return <Stack />;
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  hidden: { display: 'none' },
  header: { flexDirection: 'row', alignItems: 'center', padding: 12, borderBottomWidth: 1, borderColor: '#ddd', gap: 12 },
  back: { paddingRight: 8 },
  backText: { color: '#208AEF', fontSize: 16 },
  title: { fontSize: 18, fontWeight: '600' },
});
