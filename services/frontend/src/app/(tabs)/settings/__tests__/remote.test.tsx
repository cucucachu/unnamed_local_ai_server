import { act, type ReactTestRenderer } from 'react-test-renderer';
import { Platform } from 'react-native';

import type { CreatedWireGuardDevice, WireGuardDevice } from '@/lib/platform';

import { exists, mockFetchRoutes, press, render, requestsTo, textOf, type } from '../../../../../test-utils/screen';

jest.mock('expo-router', () => ({
  useRouter: () => ({ back: jest.fn(), push: jest.fn(), replace: jest.fn(), canGoBack: () => true }),
}));

const mockCopy = jest.fn().mockResolvedValue(undefined);
jest.mock('@/lib/clipboard', () => ({
  copyToClipboard: (text: string) => mockCopy(text),
}));

jest.mock('react-native-qrcode-svg', () => () => null, { virtual: true });

let mockRole: 'admin' | 'member' = 'member';
jest.mock('@/components/AuthProvider', () => ({
  useAuth: () => ({
    state: {
      phase: 'ready',
      setupRequired: false,
      user: { id: 'u1', username: 'alice', display_name: 'Alice', role: mockRole },
    },
  }),
}));

const mockWithStepUp = jest.fn((fn: () => Promise<unknown>) => fn());
jest.mock('@/components/StepUpProvider', () => ({
  useStepUp: () => ({ withStepUp: mockWithStepUp }),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import RemoteAccessScreen from '../remote';

function device(overrides: Partial<WireGuardDevice>): WireGuardDevice {
  return {
    id: 'd1',
    name: 'Phone',
    address: '10.13.13.2',
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

const CONFIG = [
  '[Interface]',
  'PrivateKey = CLIENTPRIV',
  'Address = 10.13.13.2/32',
  'DNS = 10.13.13.1',
  '',
  '[Peer]',
  'PublicKey = SERVERPUB',
  'AllowedIPs = 10.13.13.0/24',
  'Endpoint = homeai.local:51820',
  'PersistentKeepalive = 25',
  '',
].join('\n');

function created(overrides: Partial<CreatedWireGuardDevice> = {}): CreatedWireGuardDevice {
  return { ...device({}), config: CONFIG, ...overrides };
}

let renderer: ReactTestRenderer | null = null;
const originalConfirm = window.confirm;

beforeEach(() => {
  mockCopy.mockClear();
  mockRole = 'member';
  mockWithStepUp.mockReset();
  mockWithStepUp.mockImplementation((fn) => fn());
  Platform.OS = 'web';
  window.confirm = jest.fn().mockReturnValue(true);
  mockFetchRoutes({
    'GET /api/platform/me/wireguard-devices': { body: { devices: [device({ id: 'phone' })] } },
    'POST /api/platform/me/wireguard-devices': {
      body: created({ id: 'tablet', name: 'Tablet', address: '10.13.13.3' }),
    },
    'DELETE /api/platform/me/wireguard-devices/phone': { status: 204 },
  });
});

afterEach(() => {
  window.confirm = originalConfirm;
  act(() => renderer?.unmount());
  renderer = null;
});

describe('RemoteAccessScreen', () => {
  it('lists devices', async () => {
    renderer = await render(RemoteAccessScreen);
    expect(exists(renderer, 'remote-row-phone')).toBe(true);
    expect(textOf(renderer)).toContain('Phone');
    expect(textOf(renderer)).toContain('10.13.13.2');
  });

  it('creates a device and shows the QR plus config once', async () => {
    renderer = await render(RemoteAccessScreen);
    await type(renderer, 'remote-device-name', 'Tablet');
    await press(renderer, 'remote-create');

    expect(requestsTo(global.fetch as jest.Mock, 'POST', '/me/wireguard-devices')).toEqual([{ name: 'Tablet' }]);
    expect(exists(renderer, 'remote-created-qr')).toBe(true);
    expect(textOf(renderer)).toContain('PrivateKey = CLIENTPRIV');
    expect(textOf(renderer)).toContain('10.13.13.3');
    expect(exists(renderer, 'remote-row-tablet')).toBe(true);
  });

  it('revokes after confirm and removes the row', async () => {
    renderer = await render(RemoteAccessScreen);
    await press(renderer, 'remote-revoke-phone');

    expect(window.confirm).toHaveBeenCalled();
    expect(requestsTo(global.fetch as jest.Mock, 'DELETE', '/me/wireguard-devices/phone')).toHaveLength(1);
    expect(exists(renderer, 'remote-row-phone')).toBe(false);
  });

  it('does not revoke when confirm is dismissed', async () => {
    window.confirm = jest.fn().mockReturnValue(false);
    renderer = await render(RemoteAccessScreen);
    await press(renderer, 'remote-revoke-phone');

    expect(requestsTo(global.fetch as jest.Mock, 'DELETE', '/me/wireguard-devices/phone')).toHaveLength(0);
    expect(exists(renderer, 'remote-row-phone')).toBe(true);
  });

  it('members do not see the public HTTPS toggle', async () => {
    renderer = await render(RemoteAccessScreen);
    expect(exists(renderer, 'settings-public-https-switch')).toBe(false);
  });

  it('admins can toggle public HTTPS after step-up when a domain is configured', async () => {
    mockRole = 'admin';
    mockFetchRoutes({
      'GET /api/platform/me/wireguard-devices': { body: { devices: [device({ id: 'phone' })] } },
      'GET /api/platform/settings': {
        body: { public_https: false, domain_configured: true },
      },
      'PATCH /api/platform/admin/settings': {
        body: { public_https: true, domain_configured: true },
      },
    });
    renderer = await render(RemoteAccessScreen);
    expect(exists(renderer, 'settings-public-https-switch')).toBe(true);
    expect(textOf(renderer)).toContain('does not open the host firewall');

    const sw = renderer.root.findByProps({ testID: 'settings-public-https-switch' });
    expect(sw.props.disabled).toBe(false);
    expect(sw.props.value).toBe(false);
    await act(async () => {
      await sw.props.onValueChange(true);
    });
    expect(mockWithStepUp).toHaveBeenCalled();
    expect(requestsTo(global.fetch as jest.Mock, 'PATCH', '/admin/settings')).toEqual([
      { public_https: true },
    ]);
  });

  it('disables the switch with help text when no domain is configured', async () => {
    mockRole = 'admin';
    mockFetchRoutes({
      'GET /api/platform/me/wireguard-devices': { body: { devices: [] } },
      'GET /api/platform/settings': {
        body: { public_https: false, domain_configured: false },
      },
    });
    renderer = await render(RemoteAccessScreen);
    const sw = renderer.root.findByProps({ testID: 'settings-public-https-switch' });
    expect(sw.props.disabled).toBe(true);
    expect(textOf(renderer)).toContain('Needs a domain');
  });
});
