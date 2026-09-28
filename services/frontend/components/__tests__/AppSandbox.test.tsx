import { PROTOCOL, type BridgeHost } from '@homeai/sdk/host';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import { setSessionToken } from '@/lib/session';

// The native WebView, reduced to what the host uses: its props and
// `injectJavaScript`. Each injected script is run against a stand-in
// `window` whose `__homeaiReceive` is the runtime's receiver.
const mockInjected: string[] = [];
let mockProps: Record<string, any> = {};
jest.mock('react-native-webview', () => {
  const React = jest.requireActual('react');
  const { View } = jest.requireActual('react-native');
  const WebView = React.forwardRef(function WebView(props: Record<string, any>, ref: unknown) {
    mockProps = props;
    React.useImperativeHandle(ref, () => ({ injectJavaScript: (js: string) => mockInjected.push(js) }));
    return React.createElement(View, { testID: props.testID });
  });
  return { WebView };
});

// eslint-disable-next-line import/first -- must follow the jest.mock call above
import { AppSandbox, allowSandboxLoad } from '../AppSandbox';

const FIXED = '11111111-1111-4111-8111-111111111111';
const OTHER = '22222222-2222-4222-8222-222222222222';
const HTML = '<!doctype html><title>app</title>';

/** What the sandbox received: every injected script, evaluated. */
function delivered(): any[] {
  const out: any[] = [];
  const win = { __homeaiReceive: (wire: string) => out.push(JSON.parse(wire)) };
  for (const js of mockInjected) new Function('window', js)(win);
  return out;
}

const post = (env: object) => mockProps.onMessage({ nativeEvent: { data: JSON.stringify({ homeai: PROTOCOL, ...env }) } });

let renderer: ReactTestRenderer | null = null;
let calls: { url: string; body: any }[] = [];
const onEvent = jest.fn();
const onHost = jest.fn();

async function mount(readOnly = false) {
  await act(async () => {
    renderer = create(
      <AppSandbox instanceId={FIXED} html={HTML} readOnly={readOnly} onEvent={onEvent} onHost={onHost} onKilled={jest.fn()} />,
    );
  });
}

async function settle() {
  await act(async () => {
    for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

beforeEach(() => {
  mockInjected.length = 0;
  mockProps = {};
  calls = [];
  onEvent.mockReset();
  onHost.mockReset();
  setSessionToken('hs_native-bearer');
  global.fetch = jest.fn(async (url: string, init: { body?: string }) => {
    calls.push({ url: String(url), body: init?.body ? JSON.parse(init.body) : undefined });
    return { ok: true, status: 200, json: async () => ({ changes: 1, lastInsertRowId: 3, rows: [{ n: 1 }] }), text: async () => '' };
  }) as unknown as typeof fetch;
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
  setSessionToken(null);
});

describe('AppSandbox (native WebView)', () => {
  it('loads the inline document with the spike’s hardening and refuses every navigation but about:', async () => {
    await mount();
    expect(mockProps.source).toEqual({ html: HTML });
    expect(mockProps.originWhitelist).toEqual(['*']);
    expect(mockProps).toMatchObject({
      setSupportMultipleWindows: false,
      javaScriptCanOpenWindowsAutomatically: false,
      domStorageEnabled: false,
      incognito: true,
      cacheEnabled: false,
      thirdPartyCookiesEnabled: false,
      sharedCookiesEnabled: false,
      allowFileAccess: false,
      allowFileAccessFromFileURLs: false,
      allowUniversalAccessFromFileURLs: false,
      mixedContentMode: 'never',
    });
    const load = mockProps.onShouldStartLoadWithRequest;
    expect(load({ url: 'about:blank' })).toBe(true);
    expect(load({ url: 'about:srcdoc' })).toBe(true);
    for (const url of ['https://example.com/?c=secret', 'http://homeai.local/api/platform/me', 'file:///etc/passwd', 'data:text/html,x', 'javascript:alert(1)', 'homeai://settings', 'intent://x']) {
      expect(allowSandboxLoad({ url })).toBe(false);
      expect(load({ url })).toBe(false);
    }
  });

  it('forwards the WebView’s requests to the fixed instance and answers via injectJavaScript', async () => {
    await mount();
    await act(async () => {
      post({ kind: 'evt', event: 'runtime.ready', data: { version: 1, renderMs: 4 } });
      post({ kind: 'req', id: 1, method: 'db.run', params: { sql: 'INSERT INTO items (name) VALUES (?)', params: ['Eggs'], instance_id: OTHER, url: `/api/platform/apps/instances/${OTHER}/rpc` } });
      post({ kind: 'req', id: 2, method: 'action', params: { name: 'markAllDone', instanceId: OTHER } });
      post({ kind: 'req', id: 3, method: `instances/${OTHER}/rpc`, params: {} });
    });
    await settle();

    expect(calls.map((c) => c.url)).toEqual([
      `http://homeai.local/api/platform/apps/instances/${FIXED}/rpc`,
      `http://homeai.local/api/platform/apps/instances/${FIXED}/rpc`,
    ]);
    expect(calls.map((c) => c.body)).toEqual([
      { op: 'run', sql: 'INSERT INTO items (name) VALUES (?)', params: ['Eggs'] },
      { op: 'action', name: 'markAllDone', params: {} },
    ]);
    const replies = Object.fromEntries(delivered().filter((e) => e.kind === 'res').map((e) => [e.id, e]));
    expect(replies[1]).toMatchObject({ ok: true, result: { changes: 1, lastInsertRowId: 3 } });
    expect(replies[2]).toMatchObject({ ok: true, result: { rows: [{ n: 1 }] } });
    expect(replies[3]).toMatchObject({ ok: false, error: { code: 'method_not_allowed' } });
    expect(mockInjected.join('\n')).not.toContain('hs_native-bearer');
    expect(onEvent).toHaveBeenCalledWith('runtime.ready', { version: 1, renderMs: 4 });
  });

  it('is read-only for viewers', async () => {
    await mount(true);
    await act(async () => {
      post({ kind: 'req', id: 1, method: 'db.run', params: { sql: 'DELETE FROM items' } });
      post({ kind: 'req', id: 2, method: 'action', params: { name: 'markAllDone' } });
      post({ kind: 'req', id: 3, method: 'db.getAll', params: { sql: 'SELECT * FROM items' } });
    });
    await settle();
    const replies = Object.fromEntries(delivered().filter((e) => e.kind === 'res').map((e) => [e.id, e]));
    expect(replies[1].error.code).toBe('read_only');
    expect(replies[2].error.code).toBe('read_only');
    expect(replies[3].ok).toBe(true);
    expect(calls.map((c) => c.body.op)).toEqual(['getAll']);
  });

  it('reports runtime errors, delivers host events, and stops when unmounted', async () => {
    await mount();
    const host: BridgeHost = onHost.mock.calls[0][0];
    await act(async () => {
      post({ kind: 'evt', event: 'runtime.error', data: { message: 'boom', stack: 'at x', componentStack: '' } });
      post({ kind: 'evt', event: 'not.an.event', data: {} });
    });
    expect(onEvent.mock.calls).toEqual([['runtime.error', { message: 'boom', stack: 'at x', componentStack: '' }]]);

    host.dbChanged();
    host.loadBundle('__homeai_define(function(){})');
    expect(delivered().filter((e) => e.kind === 'evt')).toEqual([
      { homeai: PROTOCOL, kind: 'evt', event: 'db.changed', data: {} },
      { homeai: PROTOCOL, kind: 'evt', event: 'bundle.load', data: { code: '__homeai_define(function(){})' } },
    ]);

    act(() => renderer?.unmount());
    renderer = null;
    expect(onHost).toHaveBeenLastCalledWith(null);
    const count = mockInjected.length;
    host.dbChanged();
    expect(mockInjected.length).toBe(count);
  });
});
