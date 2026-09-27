import { Platform } from 'react-native';

import { ApiError, apiFetch } from '../api';
import { authErrorMessage, clearSession, getAuthStatus, login, logout, restoreSession } from '../auth';
import { sessionToken, setSessionToken } from '../session';

const mockStore = new Map<string, string>();
jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(async (key: string) => mockStore.get(key) ?? null),
  setItemAsync: jest.fn(async (key: string, value: string) => {
    mockStore.set(key, value);
  }),
  deleteItemAsync: jest.fn(async (key: string) => {
    mockStore.delete(key);
  }),
}));

const USER = {
  id: 'u1',
  username: 'alice',
  display_name: 'Alice',
  role: 'member',
  totp_enabled: false,
  disabled_at: null,
  created_at: '2026-09-27T00:00:00+00:00',
};

function response(status: number, body: unknown, headers: Record<string, string> = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: '',
    headers: { get: (name: string) => headers[name] ?? null },
    json: async () => {
      if (body === undefined) throw new Error('no body');
      return body;
    },
  };
}

const originalFetch = global.fetch;
const originalOS = Platform.OS;
let fetchMock: jest.Mock;

beforeEach(() => {
  mockStore.clear();
  setSessionToken(null);
  fetchMock = jest.fn();
  global.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  global.fetch = originalFetch;
  Platform.OS = originalOS;
});

function lastCall(): [string, RequestInit & { headers: Record<string, string> }] {
  return fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
}

describe('native bearer session', () => {
  beforeEach(() => {
    Platform.OS = 'ios';
  });

  it('login sends X-HomeAI-Client: native and stores the returned session_token', async () => {
    fetchMock.mockResolvedValueOnce(response(200, { user: USER, session_token: 'hs_abc' }));

    const user = await login({ username: 'alice', password: 'correct horse' });

    expect(user).toEqual(USER);
    const [url, init] = lastCall();
    expect(url).toMatch(/\/api\/auth\/login$/);
    expect(init.headers['X-HomeAI-Client']).toBe('native');
    expect(JSON.parse(init.body as string)).toMatchObject({ username: 'alice', password: 'correct horse' });
    expect(sessionToken()).toBe('hs_abc');
    expect(mockStore.get('homeai_session_token')).toBe('hs_abc');
  });

  it('sends the stored token as a bearer on every later request', async () => {
    fetchMock.mockResolvedValueOnce(response(200, { user: USER, session_token: 'hs_abc' }));
    await login({ username: 'alice', password: 'correct horse' });

    fetchMock.mockResolvedValueOnce(response(200, []));
    await apiFetch('/api/threads');

    expect(lastCall()[1].headers.Authorization).toBe('Bearer hs_abc');
  });

  it('restoreSession loads a persisted token from SecureStore', async () => {
    mockStore.set('homeai_session_token', 'hs_saved');

    await restoreSession();
    fetchMock.mockResolvedValueOnce(response(200, { setup_required: false, authenticated: true, user: USER }));
    await getAuthStatus();

    expect(lastCall()[1].headers.Authorization).toBe('Bearer hs_saved');
  });

  it('logout revokes server-side and deletes the stored token', async () => {
    mockStore.set('homeai_session_token', 'hs_saved');
    await restoreSession();
    fetchMock.mockResolvedValueOnce(response(204, undefined));

    await logout();

    const [url, init] = lastCall();
    expect(url).toMatch(/\/api\/auth\/logout$/);
    expect(init.method).toBe('POST');
    expect(init.headers.Authorization).toBe('Bearer hs_saved');
    expect(sessionToken()).toBeNull();
    expect(mockStore.has('homeai_session_token')).toBe(false);
  });

  it('logout still clears the local session when the server is unreachable', async () => {
    mockStore.set('homeai_session_token', 'hs_saved');
    await restoreSession();
    fetchMock.mockRejectedValueOnce(new TypeError('Network request failed'));

    await logout();

    expect(sessionToken()).toBeNull();
    expect(mockStore.has('homeai_session_token')).toBe(false);
  });

  it('clearSession forgets the token without a request', async () => {
    setSessionToken('hs_x');
    mockStore.set('homeai_session_token', 'hs_x');

    await clearSession();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(sessionToken()).toBeNull();
    expect(mockStore.size).toBe(0);
  });
});

describe('web cookie session', () => {
  beforeEach(() => {
    Platform.OS = 'web';
  });

  it('login omits the native header, stores nothing, and sends credentials', async () => {
    fetchMock.mockResolvedValueOnce(response(200, { user: USER }));

    await login({ username: 'alice', password: 'correct horse', totpCode: '123456' });

    const [url, init] = lastCall();
    expect(url).toBe('/api/auth/login');
    expect(init.headers['X-HomeAI-Client']).toBeUndefined();
    expect(init.headers.Authorization).toBeUndefined();
    expect(init.credentials).toBe('include');
    expect(JSON.parse(init.body as string).totp_code).toBe('123456');
    expect(sessionToken()).toBeNull();
  });
});

describe('auth errors', () => {
  it('surfaces the platform error code on ApiError', async () => {
    fetchMock.mockResolvedValueOnce(response(401, { detail: 'invalid_credentials' }));

    const error = await login({ username: 'alice', password: 'nope' }).catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error.detail).toBe('invalid_credentials');
  });

  it('maps codes to readable messages', () => {
    expect(authErrorMessage(new ApiError(401, 'invalid_credentials'))).toBe('Incorrect username or password.');
    expect(authErrorMessage(new ApiError(401, 'totp_required'))).toMatch(/authenticator/);
    expect(authErrorMessage(new ApiError(401, 'invalid_invite'))).toMatch(/invite link is invalid/);
    expect(authErrorMessage(new ApiError(500, 'weird_code'))).toBe('Something went wrong (weird_code).');
    expect(authErrorMessage(new TypeError('Network request failed'))).toMatch(/Couldn't reach the server/);
  });

  it('includes Retry-After in the rate-limit message', async () => {
    fetchMock.mockResolvedValueOnce(response(429, { detail: 'rate_limited' }, { 'Retry-After': '42' }));

    const error = await login({ username: 'alice', password: 'nope' }).catch((e) => e);

    expect(error.retryAfterSeconds).toBe(42);
    expect(authErrorMessage(error)).toBe('Too many attempts. Try again in 42 seconds.');
    expect(authErrorMessage(new ApiError(429, 'rate_limited'))).toMatch(/Wait a minute/);
  });
});
