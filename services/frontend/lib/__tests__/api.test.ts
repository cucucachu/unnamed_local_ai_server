import { Platform } from 'react-native';

import { apiFetch, ApiError, wsUrl } from '../api';

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
