import { act, type ReactTestRenderer } from 'react-test-renderer';

import type { User } from '@/lib/auth';
import type { Invite } from '@/lib/platform';
import { StepUpCancelledError } from '@/lib/stepUp';

import { exists, mockFetchRoutes, press, render, requestsTo, textOf, type } from '../../../../../test-utils/screen';

jest.mock('expo-router', () => ({
  useRouter: () => ({ back: jest.fn(), push: jest.fn(), replace: jest.fn(), canGoBack: () => true }),
}));

jest.mock('@/components/AuthProvider', () => ({
  useAuth: () => ({ state: { phase: 'ready', setupRequired: false, user: { id: 'admin', username: 'root', role: 'admin' } } }),
}));

const mockWithStepUp = jest.fn((fn: () => Promise<unknown>) => fn());
jest.mock('@/components/StepUpProvider', () => ({
  useStepUp: () => ({ withStepUp: mockWithStepUp }),
}));

const mockCopy = jest.fn().mockResolvedValue(undefined);
jest.mock('@/lib/clipboard', () => ({
  copyToClipboard: (text: string) => mockCopy(text),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import InvitesScreen from '../invites';
// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import UsersScreen from '../users';

function user(overrides: Partial<User>): User {
  return {
    id: 'u',
    username: 'u',
    display_name: 'U',
    role: 'member',
    totp_enabled: false,
    disabled_at: null,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function invite(overrides: Partial<Invite>): Invite {
  return {
    id: 'i',
    label: null,
    status: 'pending',
    created_by: 'admin',
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 7 * 86400000).toISOString(),
    used_at: null,
    used_by: null,
    revoked_at: null,
    ...overrides,
  };
}

let renderer: ReactTestRenderer | null = null;

beforeEach(() => {
  mockWithStepUp.mockReset();
  mockWithStepUp.mockImplementation((fn) => fn());
  mockCopy.mockClear();
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
});

describe('UsersScreen', () => {
  const ROOT = user({ id: 'admin', username: 'root', display_name: 'Root', role: 'admin' });
  const BOB = user({ id: 'u2', username: 'bob', display_name: 'Bob' });

  it('loads through step-up, and my own row has no controls', async () => {
    mockFetchRoutes({ 'GET /api/platform/admin/users': { body: { users: [ROOT, BOB] } } });
    renderer = await render(UsersScreen);

    expect(mockWithStepUp).toHaveBeenCalled();
    expect(exists(renderer, 'admin-user-row-root')).toBe(true);
    expect(exists(renderer, 'admin-user-disable-root')).toBe(false);
    expect(exists(renderer, 'admin-user-disable-bob')).toBe(true);
  });

  it('disables a user and promotes one', async () => {
    const fetchMock = mockFetchRoutes({
      'GET /api/platform/admin/users': { body: { users: [ROOT, BOB] } },
      'PATCH /api/platform/admin/users/u2': (body) => {
        const changes = body as { disabled?: boolean; role?: User['role'] };
        return {
          body: {
            ...BOB,
            ...(changes.role ? { role: changes.role } : {}),
            disabled_at: changes.disabled ? '2026-02-01T00:00:00Z' : null,
          },
        };
      },
    });
    renderer = await render(UsersScreen);

    await press(renderer, 'admin-user-disable-bob');
    expect(requestsTo(fetchMock, 'PATCH', '/admin/users/u2')).toEqual([{ disabled: true }]);
    expect(exists(renderer, 'admin-user-disabled-bob')).toBe(true);
    expect(textOf(renderer)).toContain('Enable');

    await press(renderer, 'admin-user-role-bob-admin');
    expect(requestsTo(fetchMock, 'PATCH', '/admin/users/u2')[1]).toEqual({ role: 'admin' });
  });

  it('a dismissed step-up leaves a retry', async () => {
    mockWithStepUp.mockImplementation(() => Promise.reject(new StepUpCancelledError()));
    mockFetchRoutes({});
    renderer = await render(UsersScreen);

    expect(textOf(renderer)).toContain('Confirm your password to continue.');
    expect(exists(renderer, 'settings-load-retry')).toBe(true);
  });

  it('shows last_admin', async () => {
    mockFetchRoutes({
      'GET /api/platform/admin/users': { body: { users: [ROOT, user({ id: 'u3', username: 'carol', role: 'admin' })] } },
      'PATCH /api/platform/admin/users/u3': { status: 409, body: { detail: 'last_admin' } },
    });
    renderer = await render(UsersScreen);
    await press(renderer, 'admin-user-role-carol-member');

    expect(textOf(renderer)).toContain('The server needs at least one enabled admin.');
  });
});

describe('InvitesScreen', () => {
  it('creates an invite: link from this app\'s host + QR, copied on request, and listed', async () => {
    const fetchMock = mockFetchRoutes({
      'GET /api/platform/admin/invites': { body: { invites: [] } },
      'POST /api/platform/admin/invites': {
        status: 201,
        body: { ...invite({ id: 'new', label: 'Sam' }), token: 'hi_tok', accept_url: 'http://platform:8100/invite?token=hi_tok' },
      },
    });
    renderer = await render(InvitesScreen);

    await type(renderer, 'invite-label', 'Sam');
    await press(renderer, 'invite-create');

    expect(requestsTo(fetchMock, 'POST', '/admin/invites')).toEqual([{ label: 'Sam' }]);
    expect(mockWithStepUp).toHaveBeenCalledTimes(2);
    expect(exists(renderer, 'invite-qr')).toBe(true);
    expect(textOf(renderer)).toContain('http://homeai.local/invite?token=hi_tok');
    expect(exists(renderer, 'invite-row-new')).toBe(true);

    await press(renderer, 'invite-copy');
    expect(mockCopy).toHaveBeenCalledWith('http://homeai.local/invite?token=hi_tok');

    await press(renderer, 'invite-done');
    expect(exists(renderer, 'invite-created')).toBe(false);
  });

  it('revokes a pending invite; used ones have no revoke', async () => {
    let invites = [invite({ id: 'p' }), invite({ id: 'u', status: 'used' })];
    const fetchMock = mockFetchRoutes({
      'GET /api/platform/admin/invites': () => ({ body: { invites } }),
      'DELETE /api/platform/admin/invites/p': () => {
        invites = [invite({ id: 'p', status: 'revoked' }), invites[1]];
        return { status: 204 };
      },
    });
    renderer = await render(InvitesScreen);

    expect(exists(renderer, 'invite-revoke-u')).toBe(false);
    await press(renderer, 'invite-revoke-p');

    expect(requestsTo(fetchMock, 'DELETE', '/admin/invites/p')).toHaveLength(1);
    expect(exists(renderer, 'invite-revoke-p')).toBe(false);
    expect(textOf(renderer)).toContain('revoked');
  });
});
