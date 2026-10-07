// Runtime bundle entry: provides the only modules app code can import
// (exactly ../modules.json), boots the app bundle, and wires the bridge. Built
// once per SDK version (scripts/build-runtime.mjs); the builder's smoke render
// evaluates the dev build (docs/PLATFORM.md §7).
import * as React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import * as RN from 'react-native';
import { createRoot, type Root } from 'react-dom/client';
import * as Router from './router';
import * as SDK from './sdk';
import { emit, installReceiver, on } from './bridge';
import type { SandboxConfig } from './protocol';

type AppExports = { routes: Router.RouteTable['routes']; layout?: React.ComponentType };

declare global {
  interface Window {
    __homeai_config?: SandboxConfig;
    __homeai_define?: (factory: (require: (m: string) => unknown, module: { exports: any }, exports: any) => void) => void;
  }
}

export const modules: Record<string, unknown> = {
  react: React,
  'react/jsx-runtime': jsxRuntime,
  'react-native': RN,
  'expo-router': {
    Stack: Router.Stack,
    Slot: Router.Slot,
    Link: Router.Link,
    router: Router.router,
    useRouter: Router.useRouter,
    useLocalSearchParams: Router.useLocalSearchParams,
    useGlobalSearchParams: Router.useGlobalSearchParams,
    usePathname: Router.usePathname,
  },
  'expo-sqlite': SDK.expoSqlite,
  '@homeai/sdk': {
    useDatabase: SDK.useDatabase,
    useSQLiteContext: SDK.useSQLiteContext,
    useQuery: SDK.useQuery,
    runAction: SDK.runAction,
    askAgent: SDK.askAgent,
    useSpace: SDK.useSpace,
    useUser: SDK.useUser,
    useMembers: SDK.useMembers,
    useMember: SDK.useMember,
  },
};

export function requireModule(name: string) {
  if (!Object.prototype.hasOwnProperty.call(modules, name)) {
    throw new Error(`Module "${name}" is not available in the app sandbox`);
  }
  return modules[name];
}

function errorInfo(error: unknown, componentStack?: string | null) {
  const e = error instanceof Error ? error : new Error(String(error));
  return { message: e.message, stack: e.stack ?? '', componentStack: componentStack ?? '' };
}

class ErrorBoundary extends React.Component<{ children: React.ReactNode; version: number }, { error: ReturnType<typeof errorInfo> | null }> {
  state = { error: null as ReturnType<typeof errorInfo> | null };
  static getDerivedStateFromError(error: unknown) {
    return { error: errorInfo(error) };
  }
  componentDidCatch(error: unknown, info: React.ErrorInfo) {
    emit('runtime.error', errorInfo(error, info.componentStack));
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <RN.View testID="runtime-error" style={{ padding: 16, backgroundColor: '#fee' }}>
        <RN.Text style={{ color: '#900', fontWeight: '700' }}>App crashed: {this.state.error.message}</RN.Text>
      </RN.View>
    );
  }
}

let root: Root | null = null;
let version = 0;
const config: SandboxConfig = window.__homeai_config ?? {};
if (config.space) SDK.setSpace(config.space);
if (config.user) SDK.setUser(config.user);
if (config.members) SDK.setMembers(config.members);

function mount(app: AppExports) {
  version++;
  Router.setRouteTable({ routes: app.routes }, config.initialPath ?? '/', (path) => emit('nav.changed', { path }));
  const Layout = app.layout ?? Router.DefaultLayout;
  if (!root) {
    const el = document.getElementById('root')!;
    root = createRoot(el);
  }
  const t0 = performance.now();
  root.render(
    <ErrorBoundary key={version} version={version}>
      <Layout />
    </ErrorBoundary>,
  );
  // Report after the commit so the host can time hot reloads end to end.
  requestAnimationFrame(() => emit('runtime.ready', { version, renderMs: performance.now() - t0 }));
}

window.__homeai_define = (factory) => {
  const module = { exports: {} as AppExports };
  try {
    factory(requireModule, module, module.exports);
    mount(module.exports);
  } catch (err) {
    emit('runtime.error', errorInfo(err));
  }
};

function loadBundle(code: string) {
  const s = document.createElement('script');
  s.textContent = code;
  document.head.appendChild(s);
  s.remove();
}

installReceiver();
on('bundle.load', (data: { code?: unknown }) => typeof data?.code === 'string' && loadBundle(data.code));
window.addEventListener('error', (e) => emit('runtime.error', errorInfo(e.error ?? e.message)));
window.addEventListener('unhandledrejection', (e) => emit('runtime.error', errorInfo(e.reason)));
