import { act, type ReactTestRenderer } from 'react-test-renderer';

import type { Session } from '@/lib/platform';

import { exists, mockFetchRoutes, press, render, requestsTo, textOf } from '../../../../../test-utils/screen';

jest.mock('expo-router', () => ({
  useRouter: () => ({ back: jest.fn(), push: jest.fn(), replace: jest.fn(), canGoBack: () => true }),
}));

const mockLogout = jest.fn();
jest.mock('@/components/AuthProvider', () => ({
  useAuth: () => ({ logout: mockLogout }),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import SessionsScreen from '../sessions';

function session(overrides: Partial<Session>): Session {
  const now = new Date().toISOString();
  return { id: 's', device_label: 'Web browser', created_at: now, last_seen_at: now, expires_at: now, current: false, ...overrides };
}

let renderer: ReactTestRenderer | null = null;

beforeEach(() => {
  mockLogout.mockReset();
  mockLogout.mockResolvedValue(undefined);
  mockFetchRoutes({
    'GET /api/platform/me/sessions': {
      body: { sessions: [session({ id: 'here', current: true }), session({ id: 'phone', device_label: null })] },
    },
    'DELETE /api/platform/me/sessions/here': { status: 204 },
    'DELETE /api/platform/me/sessions/phone': { status: 204 },
  });
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
});

describe('SessionsScreen', () => {
  it('lists sessions and marks this device', async () => {
    renderer = await render(SessionsScreen);

    expect(exists(renderer, 'session-row-here')).toBe(true);
    expect(exists(renderer, 'session-row-phone')).toBe(true);
    expect(textOf(renderer)).toContain('This device');
    expect(textOf(renderer)).toContain('Unknown device');
  });

  it('revoking another session removes it from the list', async () => {
    renderer = await render(SessionsScreen);
    await press(renderer, 'session-revoke-phone');

    expect(requestsTo(global.fetch as jest.Mock, 'DELETE', '/me/sessions/phone')).toHaveLength(1);
    expect(exists(renderer, 'session-row-phone')).toBe(false);
    expect(mockLogout).not.toHaveBeenCalled();
  });

  it('marks routine grants and revokes them like any session', async () => {
    mockFetchRoutes({
      'GET /api/platform/me/sessions': {
        body: {
          sessions: [
            session({ id: 'here', current: true }),
            session({ id: 'brief', device_label: 'Routine: Morning brief', routine_id: 'r-1' }),
          ],
        },
      },
      'DELETE /api/platform/me/sessions/brief': { status: 204 },
    });
    renderer = await render(SessionsScreen);

    expect(exists(renderer, 'session-routine-brief')).toBe(true);
    expect(exists(renderer, 'session-routine-here')).toBe(false);
    expect(textOf(renderer)).toContain('Routine: Morning brief');
    expect(textOf(renderer)).toContain('Runs on its own');
    await press(renderer, 'session-revoke-brief');

    expect(requestsTo(global.fetch as jest.Mock, 'DELETE', '/me/sessions/brief')).toHaveLength(1);
    expect(exists(renderer, 'session-row-brief')).toBe(false);
    expect(mockLogout).not.toHaveBeenCalled();
  });

  it('revoking this session signs out', async () => {
    renderer = await render(SessionsScreen);
    await press(renderer, 'session-revoke-here');

    expect(requestsTo(global.fetch as jest.Mock, 'DELETE', '/me/sessions/here')).toHaveLength(1);
    expect(mockLogout).toHaveBeenCalledTimes(1);
  });
});
