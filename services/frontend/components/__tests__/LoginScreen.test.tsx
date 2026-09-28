import { Platform } from 'react-native';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { createElement } from 'react';

import { exists, flush, mockFetchRoutes, press, type } from '../../test-utils/screen';

const mockLogin = jest.fn();
const mockLoginWithPasskey = jest.fn();
const mockLoginWithDevice = jest.fn();
const mockPairDevice = jest.fn();
let mockIsHost = false;
let mockPairedId: string | null = null;
jest.mock('@/components/AuthProvider', () => ({
  useAuth: () => ({
    login: mockLogin,
    loginWithPasskey: mockLoginWithPasskey,
    loginWithDevice: mockLoginWithDevice,
    pairDevice: mockPairDevice,
  }),
}));
jest.mock('@/lib/client', () => ({
  isHostApp: () => mockIsHost,
  homeAiClientHeader: () => (mockIsHost ? 'host' : 'native'),
}));
jest.mock('@/lib/auth', () => {
  const actual = jest.requireActual('@/lib/auth');
  return {
    ...actual,
    loadPairedDeviceId: () => Promise.resolve(mockPairedId),
  };
});

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import { LoginScreen } from '../LoginScreen';

let renderer: ReactTestRenderer | null = null;

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
  mockLogin.mockReset();
  mockLoginWithPasskey.mockReset();
  mockLoginWithDevice.mockReset();
  mockPairDevice.mockReset();
});

beforeEach(() => {
  Platform.OS = 'web';
  mockIsHost = false;
  mockPairedId = null;
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

  it('hides the password field when public HTTPS is on and this origin is public', async () => {
    Object.defineProperty(navigator, 'credentials', {
      configurable: true,
      value: { create: jest.fn(), get: jest.fn() },
    });
    mockFetchRoutes({
      'GET /api/auth/status': {
        body: {
          setup_required: false,
          authenticated: false,
          public_https: true,
          origin: 'public',
          webauthn: { rp_id: 'example.duckdns.org', origin_ok: true },
        },
      },
    });
    mockLoginWithPasskey.mockResolvedValue(undefined);
    await renderLogin();
    expect(exists(renderer!, 'auth-password')).toBe(false);
    expect(exists(renderer!, 'auth-passkey-submit')).toBe(false);

    await type(renderer!, 'auth-username', 'alice');
    await press(renderer!, 'auth-submit');
    expect(mockLoginWithPasskey).toHaveBeenCalledWith({ username: 'alice', totpCode: undefined });
  });
});

describe('LoginScreen host-app pairing', () => {
  beforeEach(() => {
    Platform.OS = 'android';
    mockIsHost = true;
    mockFetchRoutes({
      'GET /api/auth/status': {
        body: { setup_required: false, authenticated: false, webauthn: { origin_ok: false } },
      },
    });
  });

  it('offers Sign in with this device when a pair is stored', async () => {
    mockPairedId = 'device-1';
    mockLoginWithDevice.mockResolvedValue(undefined);
    await renderLogin();
    expect(exists(renderer!, 'host-pair-sign-in')).toBe(true);
    expect(exists(renderer!, 'host-pair-enroll')).toBe(false);
    expect(exists(renderer!, 'auth-password')).toBe(true);
    await press(renderer!, 'host-pair-sign-in');
    expect(mockLoginWithDevice).toHaveBeenCalledWith(undefined);
  });

  it('offers Pair this phone when no pair is stored', async () => {
    mockPairedId = null;
    mockPairDevice.mockResolvedValue(undefined);
    await renderLogin();
    expect(exists(renderer!, 'host-pair-sign-in')).toBe(false);
    expect(exists(renderer!, 'host-pair-enroll')).toBe(true);
    await type(renderer!, 'host-pair-payload', '{"kind":"homeai-host-pair","token":"hd_x","challenge":"ab"}');
    await press(renderer!, 'host-pair-enroll');
    expect(mockPairDevice).toHaveBeenCalled();
  });
});
