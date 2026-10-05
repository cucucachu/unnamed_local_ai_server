import { Platform } from 'react-native';

import { apiFetch, ApiError, probeSession, wsUrl } from '../api';
import { onUnauthorized, setSessionToken } from '../session';

describe('apiFetch', () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
    jest.clearAllMocks();
  });

  it('resolves with the parsed JSON body on a 2xx response', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => ({ status: 'ok' }),
    }) as unknown as typeof fetch;

    const result = await apiFetch<{ status: string }>('/api/health');

    expect(result).toEqual({ status: 'ok' });
  });

  it('throws an ApiError with status + detail (from the body) on a non-2xx response', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      json: async () => ({ detail: 'thread not found' }),
    }) as unknown as typeof fetch;

    const error = await apiFetch('/api/threads/missing').catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, detail: 'thread not found' });
  });

  it('falls back to statusText as detail when the error body has no `detail` field', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      json: async () => {
        throw new Error('body is not JSON');
      },
    }) as unknown as typeof fetch;

    const error = await apiFetch('/api/boom').catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 500, detail: 'Internal Server Error' });
  });

  it('calls fetch with the given path and init', async () => {
    const fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => ({}),
    });
    global.fetch = fetchMock as unknown as typeof fetch;

    await apiFetch('/api/threads', { method: 'POST', body: '{}' });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [calledPath, calledInit] = fetchMock.mock.calls[0];
    expect(calledPath).toContain('/api/threads');
    expect(calledInit).toMatchObject({ method: 'POST', body: '{}' });
  });
});

describe('apiFetch credentials + 401 handling', () => {
  const originalFetch = global.fetch;
  let unsubscribe: () => void = () => {};
  const listener = jest.fn();

  function respond(status: number, body: unknown) {
    const fetchMock = jest.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      statusText: '',
      json: async () => body,
    });
    global.fetch = fetchMock as unknown as typeof fetch;
    return fetchMock;
  }

  beforeEach(() => {
    listener.mockReset();
    unsubscribe = onUnauthorized(listener);
  });

  afterEach(() => {
    unsubscribe();
    setSessionToken(null);
    global.fetch = originalFetch;
  });

  it('sends credentials and merges the bearer header with caller headers', async () => {
    setSessionToken('hs_tok');
    const fetchMock = respond(200, {});

    await apiFetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' } });

    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      credentials: 'include',
      headers: { Authorization: 'Bearer hs_tok', 'Content-Type': 'application/json' },
    });
  });

  it('sends no Authorization header without a token (web cookie session)', async () => {
    const fetchMock = respond(200, {});

    await apiFetch('/api/threads');

    expect(fetchMock.mock.calls[0][1].headers).toEqual({});
  });

  it('a 401 notifies unauthorized listeners (back to Login) and still throws', async () => {
    respond(401, { detail: 'unauthenticated' });

    const error = await apiFetch('/api/threads').catch((e) => e);

    expect(error).toMatchObject({ status: 401, detail: 'unauthenticated' });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('signOutOnUnauthorized: false leaves the session alone', async () => {
    respond(401, { detail: 'invalid_credentials' });

    await apiFetch('/api/auth/login', { method: 'POST' }, { signOutOnUnauthorized: false }).catch(() => {});

    expect(listener).not.toHaveBeenCalled();
  });

  it('non-401 errors do not sign out', async () => {
    respond(403, { detail: 'admin_required' });

    await apiFetch('/api/threads').catch(() => {});

    expect(listener).not.toHaveBeenCalled();
  });

  it('probeSession signs out only on a definite authenticated: false', async () => {
    respond(200, { setup_required: false, authenticated: true });
    await probeSession();
    expect(listener).not.toHaveBeenCalled();

    global.fetch = jest.fn().mockRejectedValue(new TypeError('offline')) as unknown as typeof fetch;
    await probeSession();
    expect(listener).not.toHaveBeenCalled();

    respond(200, { setup_required: false, authenticated: false });
    await probeSession();
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe('wsUrl', () => {
  const originalOS = Platform.OS;
  const originalEnv = process.env.EXPO_PUBLIC_API_HOST;

  afterEach(() => {
    Platform.OS = originalOS;
    if (originalEnv === undefined) {
      delete process.env.EXPO_PUBLIC_API_HOST;
    } else {
      process.env.EXPO_PUBLIC_API_HOST = originalEnv;
    }
  });

  it('derives wss:// from location.protocol on https web', () => {
    Platform.OS = 'web';
    const originalLocation = window.location;
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { protocol: 'https:', host: 'homeai.local' },
    });
    try {
      expect(wsUrl('/ws/chat/abc')).toBe('wss://homeai.local/ws/chat/abc');
    } finally {
      Object.defineProperty(window, 'location', {
        configurable: true,
        value: originalLocation,
      });
    }
  });

  it('derives ws:// from location.protocol on http web', () => {
    Platform.OS = 'web';
    const originalLocation = window.location;
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { protocol: 'http:', host: 'homeai.local' },
    });
    try {
      expect(wsUrl('/ws/chat/abc')).toBe('ws://homeai.local/ws/chat/abc');
    } finally {
      Object.defineProperty(window, 'location', {
        configurable: true,
        value: originalLocation,
      });
    }
  });

  it('maps https:// EXPO_PUBLIC_API_HOST to wss:// on native', () => {
    Platform.OS = 'ios';
    process.env.EXPO_PUBLIC_API_HOST = 'https://homeai.local';
    expect(wsUrl('/ws/chat/abc')).toBe('wss://homeai.local/ws/chat/abc');
  });
});

describe('resolveApiHost', () => {
  const originalOS = Platform.OS;
  const originalEnv = process.env.EXPO_PUBLIC_API_HOST;
  const originalFetch = global.fetch;

  afterEach(() => {
    Platform.OS = originalOS;
    global.fetch = originalFetch;
    if (originalEnv === undefined) {
      delete process.env.EXPO_PUBLIC_API_HOST;
    } else {
      process.env.EXPO_PUBLIC_API_HOST = originalEnv;
    }
  });

  function freshApi(): typeof import('../api') {
    let mod: typeof import('../api') | undefined;
    jest.isolateModules(() => {
      mod = jest.requireActual('../api');
    });
    return mod!;
  }

  function reachable(...up: string[]) {
    global.fetch = jest.fn(async (url: string) => {
      if (up.some((host) => url.startsWith(host))) return { ok: true } as Response;
      throw new TypeError('Network request failed');
    }) as unknown as typeof fetch;
  }

  beforeEach(() => {
    Platform.OS = 'android';
    process.env.EXPO_PUBLIC_API_HOST = 'http://192.168.0.108, http://10.13.13.1';
  });

  it('uses the first listed host before anything is probed', () => {
    expect(freshApi().apiBase()).toBe('http://192.168.0.108');
  });

  it('prefers the LAN host when both answer', async () => {
    reachable('http://192.168.0.108', 'http://10.13.13.1');
    const api = freshApi();
    await api.resolveApiHost();
    expect(api.apiBase()).toBe('http://192.168.0.108');
    expect(api.wsUrl('/ws/x')).toBe('ws://192.168.0.108/ws/x');
  });

  it('falls back to the VPN host away from home, and back again', async () => {
    const api = freshApi();
    reachable('http://10.13.13.1');
    await api.resolveApiHost();
    expect(api.apiBase()).toBe('http://10.13.13.1');
    reachable('http://192.168.0.108');
    await api.resolveApiHost();
    expect(api.apiBase()).toBe('http://192.168.0.108');
  });

  it('keeps the current pick when nothing answers', async () => {
    const api = freshApi();
    reachable('http://10.13.13.1');
    await api.resolveApiHost();
    reachable();
    await api.resolveApiHost();
    expect(api.apiBase()).toBe('http://10.13.13.1');
  });

  it('does not probe with a single host', async () => {
    process.env.EXPO_PUBLIC_API_HOST = 'http://10.13.13.1';
    reachable();
    const api = freshApi();
    await api.resolveApiHost();
    expect(global.fetch).not.toHaveBeenCalled();
    expect(api.apiBase()).toBe('http://10.13.13.1');
  });
});
