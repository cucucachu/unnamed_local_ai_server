import { createElement } from 'react';
import { Text as RNText } from 'react-native';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import LoginRoute from '@/app/login';
import { AuthGate } from '@/components/AuthGate';
import { AuthProvider, useAuth } from '@/components/AuthProvider';
import { apiFetch } from '@/lib/api';

/** Stands in for `_layout.tsx`'s guarded Stack: the app when signed in,
 * the `login` route otherwise. */
function GuardedRoutes() {
  const { state } = useAuth();
  return state.phase === 'ready' && state.user ? createElement(RNText, null, 'APP CONTENT') : createElement(LoginRoute);
}

const USER = {
  id: 'u1',
  username: 'alice',
  display_name: 'Alice',
  role: 'member',
  totp_enabled: false,
  disabled_at: null,
  created_at: '2026-09-27T00:00:00+00:00',
};

type Route = (init: RequestInit) => { status: number; body?: unknown };
let routes: Record<string, Route>;
const fetchMock = jest.fn(async (url: string, init: RequestInit = {}) => {
  const key = `${init.method ?? 'GET'} ${url.replace(/^https?:\/\/[^/]+/, '')}`;
  const route = routes[key];
  if (!route) throw new TypeError(`Network request failed (${key})`);
  const { status, body } = route(init);
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: '',
    headers: { get: () => null },
    json: async () => {
      if (body === undefined) throw new Error('no body');
      return body;
    },
  };
});

const originalFetch = global.fetch;
let renderer: ReactTestRenderer | null = null;

beforeEach(() => {
  fetchMock.mockClear();
  global.fetch = fetchMock as unknown as typeof fetch;
  routes = {};
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
  global.fetch = originalFetch;
});

function status(body: { setup_required: boolean; authenticated: boolean; user?: unknown }) {
  routes['GET /api/auth/status'] = () => ({ status: 200, body });
}

async function renderGate(): Promise<ReactTestRenderer> {
  await act(async () => {
    renderer = create(
      createElement(AuthProvider, null, createElement(AuthGate, null, createElement(GuardedRoutes))),
    );
  });
  return renderer!;
}

function has(testID: string): boolean {
  return renderer!.root.findAllByProps({ testID }).length > 0;
}

function text(): string {
  return renderer!.root
    .findAllByType(RNText)
    .map((node) => {
      const children = node.props.children;
      return Array.isArray(children) ? children.join('') : String(children ?? '');
    })
    .join(' | ');
}

async function type(testID: string, value: string) {
  const input = renderer!.root.find((node) => node.props.testID === testID && typeof node.props.onChangeText === 'function');
  await act(async () => {
    input.props.onChangeText(value);
  });
}

async function submit() {
  const button = renderer!.root.find((node) => node.props.testID === 'auth-submit' && typeof node.props.onPress === 'function');
  await act(async () => {
    button.props.onPress();
  });
}

describe('status routing', () => {
  it('shows Setup while bootstrap is open, with a way to sign in instead', async () => {
    status({ setup_required: true, authenticated: false });
    await renderGate();

    expect(has('auth-setup-code')).toBe(true);
    expect(text()).not.toContain('APP CONTENT');

    const link = renderer!.root.find((node) => node.props.testID === 'auth-show-login' && node.props.onPress);
    await act(async () => link.props.onPress());
    expect(has('auth-login-screen')).toBe(true);
    expect(has('auth-setup-code')).toBe(false);
  });

  it('shows Login when setup is done and there is no session', async () => {
    status({ setup_required: false, authenticated: false });
    await renderGate();

    expect(has('auth-login-screen')).toBe(true);
    expect(has('auth-show-setup')).toBe(false);
    expect(text()).not.toContain('APP CONTENT');
  });

  it('shows the app for an authenticated user', async () => {
    status({ setup_required: false, authenticated: true, user: USER });
    await renderGate();

    expect(has('auth-authenticated')).toBe(true);
    expect(text()).toContain('APP CONTENT');
  });

  it('holds the app back until status answers', async () => {
    let answer: (value: unknown) => void = () => {};
    routes['GET /api/auth/status'] = () => ({ status: 200, body: { setup_required: false, authenticated: true, user: USER } });
    const gate = new Promise((resolve) => {
      answer = resolve;
    });
    fetchMock.mockImplementationOnce(async (...args) => {
      await gate;
      return fetchMock.getMockImplementation()!(...args);
    });
    await renderGate();
    expect(has('auth-loading')).toBe(true);

    await act(async () => {
      answer(null);
    });
    expect(has('auth-authenticated')).toBe(true);
  });

  it('offers Retry when the server is unreachable', async () => {
    await renderGate();
    expect(has('auth-unreachable')).toBe(true);

    status({ setup_required: false, authenticated: false });
    const retry = renderer!.root.find((node) => node.props.testID === 'auth-retry' && node.props.onPress);
    await act(async () => retry.props.onPress());

    expect(has('auth-login-screen')).toBe(true);
  });
});

describe('login', () => {
  beforeEach(() => {
    status({ setup_required: false, authenticated: false });
  });

  it('displays the error for wrong credentials and stays on Login', async () => {
    routes['POST /api/auth/login'] = () => ({ status: 401, body: { detail: 'invalid_credentials' } });
    await renderGate();

    await type('auth-username', 'alice');
    await type('auth-password', 'wrong-password');
    await submit();

    expect(has('auth-error')).toBe(true);
    expect(text()).toContain('Incorrect username or password.');
    expect(has('auth-login-screen')).toBe(true);
  });

  it('asks for a TOTP code on totp_required, then signs in with it', async () => {
    const bodies: Record<string, unknown>[] = [];
    routes['POST /api/auth/login'] = (init) => {
      const body = JSON.parse(init.body as string);
      bodies.push(body);
      return body.totp_code
        ? { status: 200, body: { user: USER } }
        : { status: 401, body: { detail: 'totp_required' } };
    };
    await renderGate();

    await type('auth-username', 'alice');
    await type('auth-password', 'correct horse');
    await submit();
    expect(has('auth-totp')).toBe(true);

    await type('auth-totp', '123456');
    await submit();

    expect(bodies[1]).toMatchObject({ username: 'alice', password: 'correct horse', totp_code: '123456' });
    expect(text()).toContain('APP CONTENT');
  });

  it('validates empty fields without a request', async () => {
    await renderGate();
    await submit();

    expect(text()).toContain('Enter your username and password.');
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/auth/login'))).toBe(false);
  });
});

describe('401 from any API call', () => {
  it('drops an authenticated user back to Login', async () => {
    status({ setup_required: false, authenticated: true, user: USER });
    routes['GET /api/threads'] = () => ({ status: 401, body: { detail: 'unauthenticated' } });
    await renderGate();
    expect(text()).toContain('APP CONTENT');

    await act(async () => {
      await apiFetch('/api/threads').catch(() => {});
    });

    expect(text()).not.toContain('APP CONTENT');
    expect(has('auth-login-screen')).toBe(true);
  });
});

describe('setup', () => {
  beforeEach(() => {
    status({ setup_required: true, authenticated: false });
  });

  async function fillSetup(confirm = 'correct horse') {
    await type('auth-setup-code', 'abcd-efgh-ijkl-mnop');
    await type('auth-username', 'admin');
    await type('auth-display-name', 'The Admin');
    await type('auth-password', 'correct horse');
    await type('auth-password-confirm', confirm);
  }

  it('submits the setup code + admin account and enters the app', async () => {
    let sent: Record<string, unknown> | null = null;
    routes['POST /api/auth/setup'] = (init) => {
      sent = JSON.parse(init.body as string);
      return { status: 200, body: { user: { ...USER, username: 'admin', role: 'admin' } } };
    };
    await renderGate();

    await fillSetup();
    await submit();

    expect(sent).toMatchObject({
      setup_code: 'abcd-efgh-ijkl-mnop',
      username: 'admin',
      display_name: 'The Admin',
      password: 'correct horse',
    });
    expect(text()).toContain('APP CONTENT');
  });

  it('rejects mismatched passwords client-side', async () => {
    await renderGate();
    await fillSetup('something else');
    await submit();

    expect(text()).toContain("Passwords don't match.");
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/auth/setup'))).toBe(false);
  });

  it('shows invalid_setup_code', async () => {
    routes['POST /api/auth/setup'] = () => ({ status: 401, body: { detail: 'invalid_setup_code' } });
    await renderGate();
    await fillSetup();
    await submit();

    expect(text()).toContain("That setup code isn't right.");
  });

  it('moves to Login when someone else already finished setup', async () => {
    routes['POST /api/auth/setup'] = () => {
      status({ setup_required: false, authenticated: false });
      return { status: 409, body: { detail: 'setup_complete' } };
    };
    await renderGate();
    await fillSetup();
    await submit();

    expect(has('auth-login-screen')).toBe(true);
    expect(has('auth-show-setup')).toBe(false);
  });
});
