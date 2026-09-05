import { createElement, useEffect } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import { SettingsProvider, useSettings } from '../SettingsProvider';

/** Routes `global.fetch` by HTTP method — same shape as `chat/
 * __tests__/index.test.tsx`'s `mockThreadsApi`. */
function mockSettingsApi(routes: Partial<Record<'GET' | 'PUT', () => { ok: boolean; status: number; body: unknown }>>): jest.Mock {
  const fetchMock = jest.fn(async (_url: string, init?: RequestInit) => {
    const method = (init?.method ?? 'GET') as 'GET' | 'PUT';
    const respond = routes[method];
    if (!respond) throw new Error(`unexpected ${method} request in this test`);
    const { ok, status, body } = respond();
    return { ok, status, statusText: ok ? 'OK' : 'Error', json: async () => body };
  });
  global.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

const DEFAULTS = { hitl_enabled: true, thinking_enabled: false, edit_mode_default: 'truncate' as const };

// A plain mutable object (not a captured outer `let` reassigned during
// render, which the `react-hooks/globals` lint rule disallows) that the
// `Probe` component below writes `useSettings()`'s return value into, so
// each `it()` block can read the hook's live value without needing its
// own `render`+`act` dance for every assertion.
const capturedRef: { current: ReturnType<typeof useSettings> | null } = { current: null };
function Probe() {
  const value = useSettings();
  // Written in an effect (not during render, which the `react-hooks/
  // immutability` lint rule disallows for anything outside this
  // component) — fine here since nothing in this test suite reads
  // `capturedRef.current` synchronously during the SAME render/`act` call
  // that updates it; every assertion below runs after an `await
  // act(...)`/`flush()` boundary, by which point this effect has run.
  useEffect(() => {
    capturedRef.current = value;
  }, [value]);
  return null;
}

async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

let activeRenderer: ReactTestRenderer | null = null;

async function renderProvider(onError?: (message: string) => void): Promise<void> {
  await act(async () => {
    activeRenderer = create(createElement(SettingsProvider, { onError }, createElement(Probe)));
  });
  await flush();
}

afterEach(() => {
  act(() => activeRenderer?.unmount());
  activeRenderer = null;
  capturedRef.current = null;
});

describe('SettingsProvider', () => {
  it('loads settings once on mount and exposes them via useSettings()', async () => {
    mockSettingsApi({ GET: () => ({ ok: true, status: 200, body: DEFAULTS }) });

    await renderProvider();

    expect(capturedRef.current?.loading).toBe(false);
    expect(capturedRef.current?.settings).toEqual(DEFAULTS);
  });

  it('applies an update optimistically, then adopts the server-merged response', async () => {
    const fetchMock = mockSettingsApi({
      GET: () => ({ ok: true, status: 200, body: DEFAULTS }),
      PUT: () => ({ ok: true, status: 200, body: { ...DEFAULTS, hitl_enabled: false } }),
    });

    await renderProvider();

    await act(async () => {
      await capturedRef.current?.updateSettings({ hitl_enabled: false });
    });

    expect(capturedRef.current?.settings).toEqual({ ...DEFAULTS, hitl_enabled: false });
    const putCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT');
    expect(putCall).toBeDefined();
  });

  it('reverts the optimistic update and reports an error on PUT failure', async () => {
    const onError = jest.fn();
    mockSettingsApi({
      GET: () => ({ ok: true, status: 200, body: DEFAULTS }),
      PUT: () => ({ ok: false, status: 422, body: { detail: 'nope' } }),
    });

    await renderProvider(onError);

    await act(async () => {
      await capturedRef.current?.updateSettings({ hitl_enabled: false }).catch(() => {});
    });

    // Reverted back to the pre-update value.
    expect(capturedRef.current?.settings).toEqual(DEFAULTS);
    expect(onError).toHaveBeenCalledWith('nope');
  });

  it('surfaces an error via onError if the initial GET fails', async () => {
    const onError = jest.fn();
    mockSettingsApi({ GET: () => ({ ok: false, status: 500, body: { detail: 'db unavailable' } }) });

    await renderProvider(onError);

    expect(capturedRef.current?.loading).toBe(false);
    expect(capturedRef.current?.settings).toBeNull();
    expect(onError).toHaveBeenCalledWith('db unavailable');
  });
});
