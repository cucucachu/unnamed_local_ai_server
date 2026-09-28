import { createElement, type ReactNode } from 'react';
import { Alert, type AlertButton } from 'react-native';
import { act, type ReactTestRenderer } from 'react-test-renderer';

import { exists, mockFetchRoutes, press, render, requestsTo, textOf } from '../../../../test-utils/screen';

const mockPush = jest.fn();
const mockParams: { current: Record<string, string> } = { current: {} };
const mockHeader: { right: (() => ReactNode) | undefined } = { right: undefined };
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush }),
  useLocalSearchParams: () => mockParams.current,
  Stack: {
    Screen: ({ options }: { options?: { headerRight?: () => ReactNode } }) => {
      mockHeader.right = options?.headerRight;
      return null;
    },
  },
}));
jest.mock('@/components/AppRunner', () => ({ AppRunner: () => null }));

/* eslint-disable import/first -- must follow the jest.mock calls above */
import AppRunnerScreen from '../apps/[instanceId]';
import AppInfoScreen from '../apps/info/[appId]';
/* eslint-enable import/first */

const C1 = '1'.repeat(40);
const C2 = '2'.repeat(40);
const C3 = '3'.repeat(40);

const space = (role: string) => ({
  id: 's1',
  slug: 'family',
  name: 'Family',
  kind: 'shared',
  gid: 3000,
  owner_user_id: null,
  role,
  created_at: '',
  archived_at: null,
});
const app = (sourcePath: string | null = '/spaces/family/Apps/groceries') => ({
  id: 'a1',
  slug: 'groceries',
  name: 'Groceries',
  source_space_id: 's1',
  source_path: sourcePath,
  working_version: sourcePath ? { version: '1.1.0', commit: C2 } : null,
});
const commit = (id: string, version: string, extra: object = {}) => ({
  id,
  parent: null,
  kind: 'build',
  subject: `Build ${version}`,
  version,
  user: 'alice',
  thread_id: null,
  reverts: null,
  created_at: '2026-09-01T10:00:00Z',
  current: false,
  ...extra,
});

let renderer: ReactTestRenderer | null = null;
beforeEach(() => {
  mockPush.mockReset();
  mockParams.current = { appId: 'a1' };
  mockHeader.right = undefined;
});
afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
  jest.restoreAllMocks();
});

function routes(role: string, extra: Record<string, unknown> = {}) {
  return mockFetchRoutes({
    'GET /api/platform/apps/a1': { body: app() },
    'GET /api/platform/spaces': { body: { spaces: [space(role)] } },
    'GET /api/platform/apps/a1/history?offset=0': {
      body: { commits: [commit(C2, '1.1.0', { current: true }), commit(C1, '1.0.0', { thread_id: 't-1' })], next_offset: 2 },
    },
    ...extra,
  });
}

describe('AppInfoScreen', () => {
  it('shows the history, newest first, with the current build marked', async () => {
    routes('owner');
    renderer = await render(AppInfoScreen);

    const text = textOf(renderer);
    expect(text).toContain('Groceries');
    expect(text).toContain('/spaces/family/Apps/groceries');
    expect(text.indexOf('Build 1.1.0')).toBeLessThan(text.indexOf('Build 1.0.0'));
    expect(text).toContain('Current');
    expect(text).toContain('Agent');
    expect(exists(renderer, 'app-revert-11111111')).toBe(true);
    expect(exists(renderer, 'app-revert-22222222')).toBe(false);
  });

  it('reverts after confirming, then reloads', async () => {
    const fetchMock = routes('editor', {
      'POST /api/platform/apps/a1/revert': {
        body: { commit: C3, ok: true, diagnostics: [], migrations: [{ migration: { status: 'pending' }, error: null }] },
      },
    });
    const alert = jest.spyOn(Alert, 'alert').mockImplementation((_t, _m, buttons?: AlertButton[]) => {
      buttons?.find((b) => b.style === 'destructive')?.onPress?.();
    });
    renderer = await render(AppInfoScreen);

    await press(renderer, 'app-revert-11111111');

    expect(alert).toHaveBeenCalledTimes(1);
    expect(requestsTo(fetchMock, 'POST', '/apps/a1/revert')).toEqual([{ commit: C1 }]);
    expect(textOf(renderer)).toContain('Restored and rebuilt. A data change is waiting for approval.');
    expect(requestsTo(fetchMock, 'GET', '/apps/a1/history?offset=0')).toHaveLength(2);
  });

  it('does nothing when the confirmation is cancelled, and reports a failed rebuild', async () => {
    const fetchMock = routes('owner', {
      'POST /api/platform/apps/a1/revert': {
        body: { commit: C3, ok: false, diagnostics: [{ message: 'TS2322 in app/index.tsx' }], migrations: [] },
      },
    });
    let answer: 'cancel' | 'destructive' = 'cancel';
    jest.spyOn(Alert, 'alert').mockImplementation((_t, _m, buttons?: AlertButton[]) => {
      buttons?.find((b) => b.style === answer)?.onPress?.();
    });
    renderer = await render(AppInfoScreen);

    await press(renderer, 'app-revert-11111111');
    expect(requestsTo(fetchMock, 'POST', '/apps/a1/revert')).toEqual([]);

    answer = 'destructive';
    await press(renderer, 'app-revert-11111111');
    expect(exists(renderer, 'app-info-error')).toBe(true);
    expect(textOf(renderer)).toContain('Restored the files, but the rebuild failed: TS2322 in app/index.tsx');
  });

  it('offers no revert to viewers', async () => {
    routes('viewer');
    renderer = await render(AppInfoScreen);
    expect(textOf(renderer)).toContain('Build 1.0.0');
    expect(exists(renderer, 'app-revert-11111111')).toBe(false);
  });

  it('pages in older commits', async () => {
    routes('owner', {
      'GET /api/platform/apps/a1/history?offset=2': { body: { commits: [commit(C3, '0.9.0')], next_offset: null } },
    });
    renderer = await render(AppInfoScreen);

    await press(renderer, 'app-history-more');

    expect(textOf(renderer)).toContain('Build 0.9.0');
    expect(exists(renderer, 'app-history-more')).toBe(false);
  });

  it('explains when the source is out of reach', async () => {
    mockFetchRoutes({
      'GET /api/platform/apps/a1': { body: app(null) },
      'GET /api/platform/spaces': { body: { spaces: [] } },
    });
    renderer = await render(AppInfoScreen);
    expect(exists(renderer, 'app-info-no-source')).toBe(true);
  });

  it('publishes the working version into selected spaces', async () => {
    const fetchMock = routes('owner', {
      'POST /api/platform/apps/a1/publish': {
        body: { version: { id: 'v1', version: '1.1.0' }, space_ids: ['s1'] },
      },
    });
    renderer = await render(AppInfoScreen);
    expect(exists(renderer, 'app-publish')).toBe(true);
    await press(renderer, 'app-publish-confirm');
    expect(requestsTo(fetchMock, 'POST', '/apps/a1/publish')).toEqual([{ space_ids: ['s1'] }]);
    expect(textOf(renderer)).toContain('Published 1.1.0.');
  });
});

describe('AppRunnerScreen', () => {
  it("has a header button to the app's info", async () => {
    mockParams.current = { instanceId: 'i1' };
    mockFetchRoutes({
      'GET /api/platform/spaces': { body: { spaces: [space('viewer')] } },
      'GET /api/platform/spaces/s1/instances': {
        body: {
          instances: [
            {
              id: 'i1',
              app_id: 'a1',
              space_id: 's1',
              tracks: 'working',
              installed_by: null,
              granted_permissions: {},
              created_at: '',
              app: { id: 'a1', slug: 'groceries', name: 'Groceries', version: '1.0.0', icon: null },
              update: null,
            },
          ],
        },
      },
    });
    renderer = await render(AppRunnerScreen);
    expect(mockHeader.right).toBeDefined();

    let header!: ReactTestRenderer;
    await act(async () => {
      const { create } = jest.requireActual('react-test-renderer');
      header = create(createElement(() => mockHeader.right!() as never));
    });
    await press(header, 'app-info-button');

    expect(mockPush).toHaveBeenCalledWith({ pathname: '/apps/info/[appId]', params: { appId: 'a1' } });
    act(() => header.unmount());
  });
});
