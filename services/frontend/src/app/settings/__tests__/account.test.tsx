import { act, type ReactTestRenderer } from 'react-test-renderer';

import type { User } from '@/lib/auth';

import { exists, isDisabled, mockFetchRoutes, press, render, requestsTo, textOf, type } from '../../../../test-utils/screen';

jest.mock('expo-router', () => ({
  useRouter: () => ({ back: jest.fn(), push: jest.fn(), replace: jest.fn(), canGoBack: () => true }),
}));

const ALICE: User = {
  id: 'u1',
  username: 'alice',
  display_name: 'Alice',
  role: 'member',
  totp_enabled: false,
  disabled_at: null,
  created_at: '2026-01-01T00:00:00Z',
};
let mockUser: User = ALICE;
const mockUpdateUser = jest.fn();
jest.mock('@/components/AuthProvider', () => ({
  useAuth: () => ({ state: { phase: 'ready', setupRequired: false, user: mockUser }, updateUser: mockUpdateUser }),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import AccountScreen from '../account';

const URI = 'otpauth://totp/HomeAI:alice?secret=JBSWY3DPEHPK3PXP&issuer=HomeAI&algorithm=SHA1&digits=6&period=30';
let renderer: ReactTestRenderer | null = null;

beforeEach(() => {
  mockUser = ALICE;
  mockUpdateUser.mockReset();
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
});

describe('AccountScreen', () => {
  it('saves a new display name and adopts the returned user', async () => {
    const fetchMock = mockFetchRoutes({
      'PATCH /api/platform/me': (body) => ({ body: { ...ALICE, display_name: (body as User).display_name } }),
    });
    renderer = await render(AccountScreen);

    expect(isDisabled(renderer, 'account-display-name-save')).toBe(true);
    await type(renderer, 'account-display-name', '  Alice B ');
    await press(renderer, 'account-display-name-save');

    expect(requestsTo(fetchMock, 'PATCH', '/api/platform/me')).toEqual([{ display_name: 'Alice B' }]);
    expect(mockUpdateUser).toHaveBeenCalledWith(expect.objectContaining({ display_name: 'Alice B' }));
    expect(textOf(renderer)).toContain('Saved.');
  });

  it('changes the password with the current one, and refuses a mismatched confirmation locally', async () => {
    const fetchMock = mockFetchRoutes({ 'PATCH /api/platform/me': { body: ALICE } });
    renderer = await render(AccountScreen);

    await type(renderer, 'account-password-current', 'old-password');
    await type(renderer, 'account-password-new', 'new-password-1');
    await type(renderer, 'account-password-confirm', 'new-password-2');
    await press(renderer, 'account-password-save');
    expect(textOf(renderer)).toContain("The new passwords don't match.");
    expect(fetchMock).not.toHaveBeenCalled();

    await type(renderer, 'account-password-confirm', 'new-password-1');
    await press(renderer, 'account-password-save');
    expect(requestsTo(fetchMock, 'PATCH', '/api/platform/me')).toEqual([
      { password: 'new-password-1', current_password: 'old-password' },
    ]);
    expect(exists(renderer, 'account-password-done')).toBe(true);
  });

  it('shows invalid_password from the server', async () => {
    mockFetchRoutes({ 'PATCH /api/platform/me': { status: 403, body: { detail: 'invalid_password' } } });
    renderer = await render(AccountScreen);

    await type(renderer, 'account-password-current', 'nope');
    await type(renderer, 'account-password-new', 'new-password-1');
    await type(renderer, 'account-password-confirm', 'new-password-1');
    await press(renderer, 'account-password-save');

    expect(textOf(renderer)).toContain('Incorrect password.');
  });

  it('enrolls TOTP: password -> QR + setup key -> code -> on', async () => {
    const fetchMock = mockFetchRoutes({
      'POST /api/platform/me/totp/enroll': { body: { secret: 'JBSWY3DPEHPK3PXP', otpauth_uri: URI } },
      'POST /api/platform/me/totp/confirm': { body: { ...ALICE, totp_enabled: true } },
    });
    renderer = await render(AccountScreen);

    await press(renderer, 'account-totp-enable');
    await type(renderer, 'account-totp-password', 'my-password');
    await press(renderer, 'account-totp-continue');

    expect(requestsTo(fetchMock, 'POST', '/totp/enroll')).toEqual([{ password: 'my-password' }]);
    expect(exists(renderer, 'account-totp-qr')).toBe(true);
    expect(textOf(renderer)).toContain('JBSWY3DPEHPK3PXP');
    expect(textOf(renderer)).toContain(URI);

    await type(renderer, 'account-totp-code', '123456');
    await press(renderer, 'account-totp-confirm');

    expect(requestsTo(fetchMock, 'POST', '/totp/confirm')).toEqual([{ code: '123456' }]);
    expect(mockUpdateUser).toHaveBeenCalledWith(expect.objectContaining({ totp_enabled: true }));
    expect(exists(renderer, 'account-totp-qr')).toBe(false);
  });

  it('keeps the QR up and explains a wrong code', async () => {
    mockFetchRoutes({
      'POST /api/platform/me/totp/enroll': { body: { secret: 'S', otpauth_uri: URI } },
      'POST /api/platform/me/totp/confirm': { status: 403, body: { detail: 'invalid_totp' } },
    });
    renderer = await render(AccountScreen);
    await press(renderer, 'account-totp-enable');
    await type(renderer, 'account-totp-password', 'pw');
    await press(renderer, 'account-totp-continue');
    await type(renderer, 'account-totp-code', '000000');
    await press(renderer, 'account-totp-confirm');

    expect(textOf(renderer)).toContain("That code didn't work.");
    expect(exists(renderer, 'account-totp-qr')).toBe(true);
    expect(mockUpdateUser).not.toHaveBeenCalled();
  });

  it('turns TOTP off with the password', async () => {
    mockUser = { ...ALICE, totp_enabled: true };
    const fetchMock = mockFetchRoutes({ 'POST /api/platform/me/totp/disable': { body: ALICE } });
    renderer = await render(AccountScreen);

    expect(exists(renderer, 'account-totp-enable')).toBe(false);
    await press(renderer, 'account-totp-disable');
    await type(renderer, 'account-totp-password', 'pw');
    await press(renderer, 'account-totp-continue');

    expect(requestsTo(fetchMock, 'POST', '/totp/disable')).toEqual([{ password: 'pw' }]);
    expect(mockUpdateUser).toHaveBeenCalledWith(expect.objectContaining({ totp_enabled: false }));
  });
});
