import { Platform } from 'react-native';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { createElement } from 'react';

import { exists, flush, mockFetchRoutes, press, type } from '../../test-utils/screen';

const mockLogin = jest.fn();
const mockLoginWithPasskey = jest.fn();
jest.mock('@/components/AuthProvider', () => ({
  useAuth: () => ({ login: mockLogin, loginWithPasskey: mockLoginWithPasskey }),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import { LoginScreen } from '../LoginScreen';

let renderer: ReactTestRenderer | null = null;

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
  mockLogin.mockReset();
  mockLoginWithPasskey.mockReset();
});

beforeEach(() => {
  Platform.OS = 'web';
});

async function renderLogin() {
  await act(async () => {
    renderer = create(createElement(LoginScreen));
  });
  await flush();
}

describe('LoginScreen passkey button', () => {
  it('hides passkey sign-in when status says passkeys are off', async () => {
    mockFetchRoutes({
      'GET /api/auth/status': {
        body: { setup_required: false, authenticated: false, webauthn: { origin_ok: false } },
      },
    });
    await renderLogin();
    expect(exists(renderer!, 'auth-passkey-submit')).toBe(false);
  });

  it('offers passkey sign-in and calls loginWithPasskey (mocked credentials)', async () => {
    Object.defineProperty(navigator, 'credentials', {
      configurable: true,
      value: { create: jest.fn(), get: jest.fn() },
    });
    mockFetchRoutes({
      'GET /api/auth/status': {
        body: {
          setup_required: false,
          authenticated: false,
          webauthn: { rp_id: 'localhost', origin_ok: true },
        },
      },
    });
    mockLoginWithPasskey.mockResolvedValue(undefined);
    await renderLogin();
    expect(exists(renderer!, 'auth-passkey-submit')).toBe(true);

    await type(renderer!, 'auth-username', 'alice');
    await press(renderer!, 'auth-passkey-submit');
    expect(mockLoginWithPasskey).toHaveBeenCalledWith({ username: 'alice', totpCode: undefined });
  });
});
