import { act, type ReactTestRenderer } from 'react-test-renderer';

import { exists, mockFetchRoutes, press, render, textOf } from '../../../../test-utils/screen';

const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush }),
  useFocusEffect: (effect: () => void) => {
    const { useEffect } = jest.requireActual('react');
    useEffect(effect, [effect]);
  },
}));

// eslint-disable-next-line import/first -- must follow the jest.mock call above
import HomeScreen from '../apps/index';

const space = (id: string, kind: 'personal' | 'shared', name: string, role: string, archived_at: string | null = null) => ({
  id,
  slug: `${kind}-${id}`,
  name,
  kind,
  gid: 3000,
  owner_user_id: null,
  role,
  created_at: '',
  archived_at,
});
const instance = (id: string, spaceId: string, slug: string, name: string) => ({
  id,
  app_id: `app-${slug}`,
  space_id: spaceId,
  tracks: 'working',
  installed_by: null,
  granted_permissions: {},
  created_at: '',
  app: { id: `app-${slug}`, slug, name, version: '1.0.0', icon: 'list-outline' },
  update: null,
});

let renderer: ReactTestRenderer | null = null;
beforeEach(() => mockPush.mockReset());
afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
});

describe('HomeScreen', () => {
  it('lists system apps and installed instances grouped by space, Personal first, and opens the runner', async () => {
    mockFetchRoutes({
      'GET /api/platform/spaces': {
        body: {
          spaces: [
            space('s2', 'shared', 'Family', 'viewer'),
            space('s1', 'personal', 'Alice', 'owner'),
            space('s3', 'shared', 'Empty', 'editor'),
            space('s4', 'shared', 'Old', 'owner', '2026-01-01T00:00:00Z'),
          ],
        },
      },
      'GET /api/platform/spaces/s1/instances': { body: { instances: [instance('i1', 's1', 'groceries', 'Groceries')] } },
      'GET /api/platform/spaces/s2/instances': { body: { instances: [instance('i2', 's2', 'chores', 'Chores')] } },
      'GET /api/platform/spaces/s3/instances': { body: { instances: [] } },
    });
    renderer = await render(HomeScreen);

    const text = textOf(renderer);
    expect(exists(renderer, 'home-launcher')).toBe(true);
    expect(exists(renderer, 'home-system')).toBe(true);
    expect(text.indexOf('Personal')).toBeLessThan(text.indexOf('Family'));
    expect(text).toContain('Groceries');
    expect(text).toContain('Chores');
    expect(text).toContain('View only');
    expect(exists(renderer, 'apps-space-shared-s2')).toBe(true);
    expect(exists(renderer, 'apps-space-shared-s3')).toBe(false);
    expect(exists(renderer, 'home-space-shared-s3')).toBe(true);
    expect(exists(renderer, 'home-space-shared-s4')).toBe(false);

    // M19-01: Chat is a tab, not a tile.
    expect(exists(renderer, 'home-open-chat')).toBe(false);
    await press(renderer, 'home-open-files');
    expect(mockPush).toHaveBeenCalledWith('/files');
    await press(renderer, 'home-open-routines');
    expect(mockPush).toHaveBeenCalledWith('/routines');
    await press(renderer, 'home-open-settings');
    expect(mockPush).toHaveBeenCalledWith('/settings');

    await press(renderer, 'apps-open-chores');
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/apps/[instanceId]', params: { instanceId: 'i2' } });
  });

  it('filters installed apps with the space switcher', async () => {
    mockFetchRoutes({
      'GET /api/platform/spaces': {
        body: { spaces: [space('s2', 'shared', 'Family', 'editor'), space('s1', 'personal', 'Alice', 'owner')] },
      },
      'GET /api/platform/spaces/s1/instances': { body: { instances: [instance('i1', 's1', 'groceries', 'Groceries')] } },
      'GET /api/platform/spaces/s2/instances': { body: { instances: [instance('i2', 's2', 'chores', 'Chores')] } },
    });
    renderer = await render(HomeScreen);
    expect(exists(renderer, 'home-space-switcher')).toBe(true);
    expect(exists(renderer, 'apps-space-personal-s1')).toBe(true);
    expect(exists(renderer, 'apps-space-shared-s2')).toBe(true);

    await press(renderer, 'home-space-shared-s2');
    expect(exists(renderer, 'apps-space-shared-s2')).toBe(true);
    expect(exists(renderer, 'apps-space-personal-s1')).toBe(false);
    expect(textOf(renderer)).toContain('Chores');
    expect(textOf(renderer)).not.toContain('Groceries');
    expect(exists(renderer, 'home-system')).toBe(true);

    await press(renderer, 'home-space-all');
    expect(exists(renderer, 'apps-space-personal-s1')).toBe(true);
    expect(exists(renderer, 'apps-space-shared-s2')).toBe(true);
  });

  it('opens the catalog and an update sheet', async () => {
    const withUpdate = {
      ...instance('i2', 's2', 'chores', 'Chores'),
      update: { id: 'v2', version: '1.1.0', permissions: {} },
    };
    mockFetchRoutes({
      'GET /api/platform/spaces': {
        body: { spaces: [space('s2', 'shared', 'Family', 'editor'), space('s1', 'personal', 'Alice', 'owner')] },
      },
      'GET /api/platform/spaces/s1/instances': { body: { instances: [instance('i1', 's1', 'groceries', 'Groceries')] } },
      'GET /api/platform/spaces/s2/instances': { body: { instances: [withUpdate] } },
    });
    renderer = await render(HomeScreen);
    expect(textOf(renderer)).toContain('Catalog · 1 update');
    expect(exists(renderer, 'apps-catalog')).toBe(true);
    await press(renderer, 'apps-catalog');
    expect(mockPush).toHaveBeenCalledWith('/apps/catalog');
    await press(renderer, 'apps-update-chores');
    expect(mockPush).toHaveBeenCalledWith({
      pathname: '/apps/update',
      params: { spaceId: 's2', instanceId: 'i2' },
    });
  });

  it('has an empty state for installed apps', async () => {
    mockFetchRoutes({
      'GET /api/platform/spaces': { body: { spaces: [space('s1', 'personal', 'Alice', 'owner')] } },
      'GET /api/platform/spaces/s1/instances': { body: { instances: [] } },
    });
    renderer = await render(HomeScreen);
    expect(exists(renderer, 'apps-empty')).toBe(true);
    expect(exists(renderer, 'home-system')).toBe(true);
    expect(exists(renderer, 'home-space-switcher')).toBe(false);
  });
});
