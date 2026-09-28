import { Platform } from 'react-native';

import { ApiError } from '../api';
import {
  addMember,
  adminCreateInvite,
  adminUpdateUser,
  confirmTotp,
  createSpace,
  enrollTotp,
  inviteLink,
  isReadOnly,
  listSessions,
  platformErrorMessage,
  removeMember,
  revokeSession,
  slugify,
  updateMe,
  updateMemberRole,
} from '../platform';
import { StepUpCancelledError } from '../stepUp';

const originalFetch = global.fetch;
let fetchMock: jest.Mock;

beforeEach(() => {
  fetchMock = jest.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: 'OK',
    headers: { get: () => null },
    json: async () => ({ sessions: [{ id: 's1' }] }),
  });
  global.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  global.fetch = originalFetch;
});

function lastCall(): { url: string; method: string; body: unknown } {
  const [url, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
  return { url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(init.body) : undefined };
}

describe('platform client requests', () => {
  it.each([
    ['updateMe', () => updateMe({ display_name: 'Al' }), 'PATCH', '/api/platform/me', { display_name: 'Al' }],
    ['revokeSession', () => revokeSession('s1'), 'DELETE', '/api/platform/me/sessions/s1', undefined],
    ['enrollTotp', () => enrollTotp('pw'), 'POST', '/api/platform/me/totp/enroll', { password: 'pw' }],
    ['confirmTotp', () => confirmTotp('123456'), 'POST', '/api/platform/me/totp/confirm', { code: '123456' }],
    ['createSpace', () => createSpace({ slug: 'fam', name: 'Fam' }), 'POST', '/api/platform/spaces', { slug: 'fam', name: 'Fam' }],
    ['addMember', () => addMember('sp', 'u2', 'editor'), 'POST', '/api/platform/spaces/sp/members', { user_id: 'u2', role: 'editor' }],
    ['updateMemberRole', () => updateMemberRole('sp', 'u2', 'viewer'), 'PATCH', '/api/platform/spaces/sp/members/u2', { role: 'viewer' }],
    ['removeMember', () => removeMember('sp', 'u2'), 'DELETE', '/api/platform/spaces/sp/members/u2', undefined],
    ['adminUpdateUser', () => adminUpdateUser('u2', { disabled: true }), 'PATCH', '/api/platform/admin/users/u2', { disabled: true }],
    ['adminCreateInvite', () => adminCreateInvite('Sam'), 'POST', '/api/platform/admin/invites', { label: 'Sam' }],
    ['adminCreateInvite (no label)', () => adminCreateInvite(), 'POST', '/api/platform/admin/invites', {}],
  ])('%s', async (_name, call, method, path, body) => {
    await call();
    const sent = lastCall();
    expect(sent.method).toBe(method);
    expect(sent.url.endsWith(path)).toBe(true);
    expect(sent.body).toEqual(body);
  });

  it('unwraps list envelopes', async () => {
    await expect(listSessions()).resolves.toEqual([{ id: 's1' }]);
  });
});

describe('inviteLink', () => {
  it('uses EXPO_PUBLIC_API_HOST on native', () => {
    const previous = process.env.EXPO_PUBLIC_API_HOST;
    process.env.EXPO_PUBLIC_API_HOST = 'http://192.168.1.42/';
    try {
      expect(Platform.OS).not.toBe('web');
      expect(inviteLink('hi_a+b')).toBe('http://192.168.1.42/invite?token=hi_a%2Bb');
    } finally {
      process.env.EXPO_PUBLIC_API_HOST = previous;
    }
  });
});

describe('slugify', () => {
  it.each([
    ['Family Photos', 'family-photos'],
    ['  --Hello, World!--  ', 'hello-world'],
    ['Ünïcode Späce', 'n-code-sp-ce'],
    ['a'.repeat(50), 'a'.repeat(40)],
  ])('%s -> %s', (name, slug) => {
    expect(slugify(name)).toBe(slug);
  });
});

describe('isReadOnly', () => {
  it('is true for viewers and anyone without a role', () => {
    const space = (role: 'owner' | 'editor' | 'viewer' | null) => ({
      id: 's1',
      slug: 'home',
      name: 'Home',
      kind: 'shared' as const,
      gid: 3000,
      owner_user_id: null,
      role,
      created_at: '',
      archived_at: null,
    });
    expect([isReadOnly(space('owner')), isReadOnly(space('editor')), isReadOnly(space('viewer')), isReadOnly(space(null))]).toEqual([
      false,
      false,
      true,
      true,
    ]);
  });
});

describe('platformErrorMessage', () => {
  it('maps codes, rate limits, cancellations, and network failures', () => {
    expect(platformErrorMessage(new ApiError(409, 'last_owner'))).toBe('A space needs at least one owner.');
    expect(platformErrorMessage(new ApiError(429, 'rate_limited', 30))).toContain('30 seconds');
    expect(platformErrorMessage(new ApiError(418, 'teapot'))).toContain('teapot');
    expect(platformErrorMessage(new StepUpCancelledError())).toBe('Confirm your password to continue.');
    expect(platformErrorMessage(new TypeError('Network request failed'))).toContain("Couldn't reach the server");
  });
});
