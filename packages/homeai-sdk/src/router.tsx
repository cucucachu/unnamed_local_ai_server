// expo-router-compatible shim: Stack (+ Stack.Screen), Tabs (+ Tabs.Screen),
// Slot, Link, router / useRouter, useLocalSearchParams,
// useGlobalSearchParams, usePathname. The route table is generated at build
// time from app/**: routes by file name (groups included, e.g.
// "(tabs)/index") and the layouts by folder ("" is app/_layout.tsx).
//
// One history (`stack`) is the source of truth. Each layout renders the part
// of it that belongs to its folder: an entry belongs to the layout's child
// that holds its route file, either a nested layout's folder or the route
// itself. Stack shows one screen per run of entries with the same child;
// Tabs shows the trailing run of its active tab and remembers each tab's
// own entries when you switch away.
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
import { Ionicons } from './icons';

export type RouteDef = { name: string; segments: string[]; component: ComponentType<any> };
export type RouteTable = { layouts: Record<string, ComponentType<any>>; routes: RouteDef[] };
type Params = Record<string, string | string[]>;
type Entry = { key: number; name: string | null; path: string; params: Params };
export type Href = string | { pathname: string; params?: Record<string, unknown> };
type HeaderSlot = ReactNode | ((props: { tintColor: string }) => ReactNode);
type ScreenOptions = {
  title?: string;
  headerShown?: boolean;
  presentation?: 'card' | 'modal';
  headerLeft?: HeaderSlot;
  headerRight?: HeaderSlot;
  // Tabs only
  icon?: string;
  tabBarIcon?: (props: { color: string; size: number; focused: boolean }) => ReactNode;
  tabBarBadge?: string | number;
  href?: null;
};

const TINT = '#208AEF';
const INACTIVE = '#8E8E93';
const NOT_FOUND = '+not-found';

let table: RouteTable = { layouts: {}, routes: [] };
let stack: Entry[] = [];
let nextKey = 1;
const subs = new Set<() => void>();
let onChange: (path: string) => void = () => {};
// Tabs layout folder -> tab -> that tab's entries when it was last active.
const tabMemory = new Map<string, Map<string, Entry[]>>();

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

const join = (dir: string, name: string) => (dir ? `${dir}/${name}` : name);

/** Which child of the layout at `dir` holds `name`: a nested layout's folder (relative to `dir`) or the route's name relative to `dir`. */
function childOf(name: string | null, dir: string): string {
  if (name === null) return NOT_FOUND;
  const rest = dir ? name.slice(dir.length + 1) : name;
  const parts = rest.split('/');
  for (let i = 1; i < parts.length; i++) {
    const sub = parts.slice(0, i).join('/');
    if (join(dir, sub) in table.layouts) return sub;
  }
  return rest;
}

const isLayout = (dir: string, child: string) => child !== NOT_FOUND && join(dir, child) in table.layouts;

/** Consecutive entries with the same child of `dir`, oldest first. */
function runs(entries: Entry[], dir: string): { child: string; entries: Entry[] }[] {
  const out: { child: string; entries: Entry[] }[] = [];
  for (const e of entries) {
    const child = childOf(e.name, dir);
    const last = out[out.length - 1];
    if (last && last.child === child && isLayout(dir, child)) last.entries.push(e);
    else out.push({ child, entries: [e] });
  }
  return out;
}

function commit(next: Entry[]) {
  stack = next;
  subs.forEach((fn) => fn());
  onChange(stack[stack.length - 1]?.path ?? '/');
}

/** Replace `old` (a consecutive slice of the history) with `next`. */
function splice(old: Entry[], next: Entry[]) {
  const i = stack.indexOf(old[0]);
  if (i < 0) return;
  commit([...stack.slice(0, i), ...next, ...stack.slice(i + old.length)]);
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
  tabMemory.clear();
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
// The layout being rendered: its folder and the entries it shows.
const NavContext = createContext<{ dir: string; entries: Entry[] } | null>(null);

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

function useNav() {
  const s = useStack();
  return useContext(NavContext) ?? { dir: '', entries: s };
}

function NotFound({ path }: { path: string }) {
  return (
    <View style={{ padding: 16 }}>
      <Text>Unmatched route: {path}</Text>
    </View>
  );
}

/** The layout for `dir` (a Stack when the folder has none, as in expo-router) over `entries`. */
function LayoutHost({ dir, entries }: { dir: string; entries: Entry[] }) {
  const L = table.layouts[dir] ?? DefaultLayout;
  return (
    <NavContext.Provider value={{ dir, entries }}>
      <L />
    </NavContext.Provider>
  );
}

/** What a navigator shows for one child: the nested layout, or the route's screen. */
function ChildContent({ dir, child, entries }: { dir: string; child: string; entries: Entry[] }) {
  if (isLayout(dir, child)) return <LayoutHost dir={join(dir, child)} entries={entries} />;
  const entry = entries[entries.length - 1];
  const C = table.routes.find((r) => r.name === entry.name)?.component;
  return C ? <C /> : <NotFound path={entry.path} />;
}

function renderSlot(slot: HeaderSlot | undefined) {
  return typeof slot === 'function' ? slot({ tintColor: TINT }) : (slot ?? null);
}

function Header({ title, opts, back, close }: { title: string; opts: ScreenOptions; back: boolean; close: boolean }) {
  return (
    <View style={styles.header}>
      {opts.headerLeft !== undefined ? (
        <View testID="header-left">{renderSlot(opts.headerLeft)}</View>
      ) : (
        back && (
          <Pressable testID="header-back" onPress={router.back} style={styles.back}>
            <Text style={styles.backText}>‹ Back</Text>
          </Pressable>
        )
      )}
      <Text role="heading" style={styles.title} numberOfLines={1}>
        {title}
      </Text>
      {opts.headerRight !== undefined && <View testID="header-right">{renderSlot(opts.headerRight)}</View>}
      {close && (
        <Pressable testID="header-close" accessibilityLabel="Close" onPress={router.back} style={styles.back}>
          <Text style={styles.backText}>Close</Text>
        </Pressable>
      )}
    </View>
  );
}

/** One screen: the options its layout gave it, merged with any it sets itself via Stack.Screen. */
function ScreenHost({ dir, child, entries, base, chrome }: {
  dir: string;
  child: string;
  entries: Entry[];
  base: ScreenOptions;
  chrome: (opts: ScreenOptions, content: ReactNode) => ReactNode;
}) {
  const [own, setOwn] = useState<ScreenOptions>({});
  const entry = entries[entries.length - 1];
  return (
    <EntryContext.Provider value={{ entry, setOptions: setOwn }}>
      {chrome({ ...base, ...own }, <ChildContent dir={dir} child={child} entries={entries} />)}
    </EntryContext.Provider>
  );
}

function optionsFrom(children: ReactNode, Screen: unknown) {
  const out: Record<string, ScreenOptions> = {};
  const order: string[] = [];
  Children.forEach(children, (c) => {
    if (isValidElement(c) && c.type === Screen) {
      const p = c.props as { name: string; options?: ScreenOptions };
      out[p.name] = p.options ?? {};
      order.push(p.name);
    }
  });
  return { byName: out, order };
}

export function Stack({ children, screenOptions = {} }: { children?: ReactNode; screenOptions?: ScreenOptions }) {
  const { dir, entries } = useNav();
  const { byName } = optionsFrom(children, StackScreen);
  const screens = runs(entries, dir);
  const top = screens.length - 1;
  const isModal = (i: number) => (byName[screens[i].child]?.presentation ?? screenOptions.presentation) === 'modal';
  return (
    <View style={styles.screen}>
      {screens.map((s, i) => {
        // A modal shows over the screen beneath it.
        const visible = i === top || (i === top - 1 && isModal(top));
        const modal = isModal(i);
        // A nested navigator draws its own header.
        const nested = isLayout(dir, s.child);
        return (
          <ScreenHost
            key={s.entries[0].key}
            dir={dir}
            child={s.child}
            entries={s.entries}
            base={{ ...(nested ? { headerShown: false } : {}), ...screenOptions, ...byName[s.child] }}
            chrome={(opts, content) => {
              const body = (
                <>
                  {opts.headerShown !== false && (
                    <Header title={opts.title ?? s.child} opts={opts} back={!modal && stack.indexOf(s.entries[0]) > 0} close={modal} />
                  )}
                  <View style={styles.screen}>{content}</View>
                </>
              );
              if (modal) {
                return (
                  <View style={[styles.overlay, !visible && styles.hidden]}>
                    <View testID="modal" style={styles.sheet}>{body}</View>
                  </View>
                );
              }
              return <View style={[styles.screen, !visible && styles.hidden]}>{body}</View>;
            }}
          />
        );
      })}
    </View>
  );
}

function StackScreen({ options }: { name?: string; options?: ScreenOptions }) {
  const ctx = useContext(EntryContext);
  const key = JSON.stringify(options ?? {}, (_k, v) => (typeof v === 'function' ? String(v) : isValidElement(v) ? '<el>' : v));
  useEffect(() => {
    if (ctx && options) ctx.setOptions(options);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return null;
}
Stack.Screen = StackScreen;

/** The path a tab opens at first: the route itself, or the shortest static route under a nested layout. */
function tabPath(dir: string, child: string): string | null {
  const candidates = table.routes
    .filter((r) => childOf(r.name, dir) === child && !r.segments.some((s) => s.startsWith('[')))
    .sort((a, b) => a.segments.length - b.segments.length);
  return candidates.length ? '/' + candidates[0].segments.join('/') : null;
}

export function Tabs({ children, screenOptions = {} }: { children?: ReactNode; screenOptions?: ScreenOptions }) {
  const { dir, entries } = useNav();
  const { byName, order } = optionsFrom(children, TabsScreen);
  const declared = order.length
    ? order
    : [...new Set(table.routes.filter((r) => !dir || r.name.startsWith(`${dir}/`)).map((r) => childOf(r.name, dir)))];
  const tabs = declared.filter((t) => byName[t]?.href !== null && tabPath(dir, t) !== null);
  const all = runs(entries, dir);
  const current = all[all.length - 1];
  const active = current?.child ?? tabs[0];
  // The active tab's entries: its trailing run (pushing into another tab ends this one's run).
  const shown = current ? current.entries : [];
  let memory = tabMemory.get(dir);
  if (!memory) tabMemory.set(dir, (memory = new Map()));
  if (current) memory.set(active, shown);
  const select = (t: string) => {
    if (t === active) {
      // Tapping the active tab pops it to its first screen.
      if (shown.length > 1) splice(shown, [shown[0]]);
      return;
    }
    const remembered = memory!.get(t);
    const path = tabPath(dir, t)!;
    splice(entries, remembered?.length ? remembered : [resolve(path)]);
  };
  const opts = { ...screenOptions, ...byName[active] };
  return (
    <View style={styles.screen}>
      {current && (
        <ScreenHost
          key={`${dir}:${active}`}
          dir={dir}
          child={active}
          entries={shown}
          base={{ ...(isLayout(dir, active) ? { headerShown: false } : {}), ...opts }}
          chrome={(o, content) => (
            <>
              {o.headerShown !== false && <Header title={o.title ?? active} opts={o} back={stack.indexOf(entries[0]) > 0} close={false} />}
              <View style={styles.screen}>{content}</View>
            </>
          )}
        />
      )}
      <View role="tablist" style={styles.tabBar}>
        {tabs.map((t) => {
          const o = { ...screenOptions, ...byName[t] };
          const focused = t === active;
          const color = focused ? TINT : INACTIVE;
          return (
            <Pressable
              key={t}
              testID={`tab-${t}`}
              role="tab"
              aria-selected={focused}
              accessibilityLabel={o.title ?? t}
              onPress={() => select(t)}
              style={styles.tab}
            >
              <View>
                {o.tabBarIcon ? o.tabBarIcon({ color, size: 24, focused }) : o.icon ? <Ionicons name={o.icon} size={24} color={color} /> : null}
                {o.tabBarBadge !== undefined && (
                  <View style={styles.badge}>
                    <Text style={styles.badgeText}>{String(o.tabBarBadge)}</Text>
                  </View>
                )}
              </View>
              <Text style={[styles.tabLabel, { color }]}>{o.title ?? t}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

function TabsScreen(_props: { name?: string; options?: ScreenOptions }) {
  return null;
}
Tabs.Screen = TabsScreen;

export function Slot() {
  const { dir, entries } = useNav();
  const all = runs(entries, dir);
  const last = all[all.length - 1];
  return last ? <ScreenHost key={last.entries[0].key} dir={dir} child={last.child} entries={last.entries} base={{}} chrome={(_o, c) => c} /> : null;
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

/** Default layout for a folder without _layout.tsx (expo-router renders a Stack). */
export function DefaultLayout() {
  return <Stack />;
}

/** What the runtime renders: the root layout over the whole history. */
export function Root() {
  const s = useStack();
  return <LayoutHost dir="" entries={s} />;
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  hidden: { display: 'none' },
  overlay: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.35)', justifyContent: 'flex-end' },
  sheet: { height: '92%', backgroundColor: '#fff', borderTopLeftRadius: 12, borderTopRightRadius: 12, overflow: 'hidden' },
  header: { flexDirection: 'row', alignItems: 'center', padding: 12, borderBottomWidth: 1, borderColor: '#ddd', gap: 12 },
  back: { paddingRight: 8 },
  backText: { color: TINT, fontSize: 16 },
  title: { flex: 1, fontSize: 18, fontWeight: '600' },
  tabBar: { flexDirection: 'row', borderTopWidth: 1, borderColor: '#ddd', backgroundColor: '#fafafa' },
  tab: { flex: 1, alignItems: 'center', paddingVertical: 6, gap: 2 },
  tabLabel: { fontSize: 11 },
  badge: { position: 'absolute', top: -4, right: -10, minWidth: 16, paddingHorizontal: 4, borderRadius: 8, backgroundColor: '#E5484D', alignItems: 'center' },
  badgeText: { color: '#fff', fontSize: 10, fontWeight: '700' },
});
