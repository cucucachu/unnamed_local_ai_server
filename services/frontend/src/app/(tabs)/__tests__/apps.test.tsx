import { act, type ReactTestRenderer } from 'react-test-renderer';
import { Alert } from 'react-native';

import { exists, flush, mockFetchRoutes, press, render, requestsTo, textOf } from '../../../../test-utils/screen';

const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush }),
  useFocusEffect: (effect: () => void) => {
    const { useEffect } = jest.requireActual('react');
    useEffect(effect, [effect]);
  },
}));

// eslint-disable-next-line import/first -- must follow the jest.mock call above
import { gridColumns } from '@/components/AppGrid';
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
const instance = (id: string, spaceId: string, slug: string, name: string, icon: string | null = 'list-outline') => ({
  id,
  app_id: `app-${slug}`,
  space_id: spaceId,
  tracks: 'working',
  installed_by: null,
  granted_permissions: {},
  created_at: '',
  app: { id: `app-${slug}`, slug, name, version: '1.0.0', icon },
  update: null,
});

let renderer: ReactTestRenderer | null = null;

async function longPress(target: ReactTestRenderer, testID: string): Promise<void> {
  await act(async () => {
    target.root.find((node) => node.props.testID === testID && typeof node.props.onLongPress === 'function').props.onLongPress();
  });
  await flush();
}
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
    expect(textOf(renderer)).toContain('1 update');
    expect(exists(renderer, 'apps-catalog-badge')).toBe(true);
    expect(exists(renderer, 'apps-open-chores-badge')).toBe(true);
    await press(renderer, 'apps-catalog');
    expect(mockPush).toHaveBeenCalledWith('/apps/catalog');
    await longPress(renderer, 'apps-open-chores');
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

describe('HomeScreen grid (M19-04)', () => {
  const personal = space('s1', 'personal', 'Alice', 'owner');
  const family = space('s2', 'shared', 'Family', 'viewer');
  const appRoute = (slug: string, sourcePath: string | null) => ({
    body: { id: `app-${slug}`, slug, name: slug, source_space_id: 's1', source_path: sourcePath, working_version: null },
  });

  it('is 4 columns on a phone and more on wider screens', () => {
    expect(gridColumns(390)).toBe(4);
    expect(gridColumns(800)).toBe(6);
    expect(gridColumns(1280)).toBe(8);
  });

  it('shows an app icon from homeai.icon, else the first letter', async () => {
    mockFetchRoutes({
      'GET /api/platform/spaces': { body: { spaces: [personal] } },
      'GET /api/platform/spaces/s1/instances': {
        body: {
          instances: [
            instance('i1', 's1', 'groceries', 'Groceries', 'cart-outline'),
            instance('i2', 's1', 'notes', 'notes', null),
            instance('i3', 's1', 'odd', 'Odd one', 'not-a-real-icon'),
          ],
        },
      },
    });
    renderer = await render(HomeScreen);
    expect(exists(renderer, 'app-icon-glyph-groceries')).toBe(true);
    expect(exists(renderer, 'app-icon-letter-notes')).toBe(true);
    expect(exists(renderer, 'app-icon-letter-odd')).toBe(true);
    const letter = renderer.root.find((node) => node.props.testID === 'app-icon-letter-notes' && typeof node.type === 'string');
    expect(letter.props.children).toBe('N');
    expect(exists(renderer, 'app-icon-glyph-files')).toBe(true);
  });

  it('long press offers open, rebuild, App info and uninstall', async () => {
    const fetchMock = mockFetchRoutes({
      'GET /api/platform/spaces': { body: { spaces: [personal] } },
      'GET /api/platform/spaces/s1/instances': { body: { instances: [instance('i1', 's1', 'groceries', 'Groceries')] } },
      'GET /api/platform/apps/app-groceries': appRoute('groceries', '/personal/apps/groceries'),
      'POST /api/platform/apps/app-groceries/build': { body: { ok: true, diagnostics: [] } },
      'DELETE /api/platform/spaces/s1/instances/i1': { status: 204 },
    });
    const alert = jest.spyOn(Alert, 'alert').mockImplementation((_title, _message, buttons) => {
      buttons?.find((button) => button.style === 'destructive')?.onPress?.();
    });
    renderer = await render(HomeScreen);

    await longPress(renderer, 'apps-open-groceries');
    expect(exists(renderer, 'app-sheet')).toBe(true);
    expect(exists(renderer, 'app-sheet-rebuild')).toBe(true);
    await press(renderer, 'app-sheet-rebuild');
    expect(requestsTo(fetchMock, 'POST', '/api/platform/apps/app-groceries/build')).toHaveLength(1);
    expect(textOf(renderer)).toContain('Rebuilt Groceries');
    expect(exists(renderer, 'app-sheet')).toBe(false);

    await longPress(renderer, 'apps-open-groceries');
    await press(renderer, 'app-sheet-info');
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/apps/info/[appId]', params: { appId: 'app-groceries' } });

    await longPress(renderer, 'apps-open-groceries');
    await press(renderer, 'app-sheet-open');
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/apps/[instanceId]', params: { instanceId: 'i1' } });

    await longPress(renderer, 'apps-open-groceries');
    await press(renderer, 'app-sheet-uninstall');
    expect(requestsTo(fetchMock, 'DELETE', '/api/platform/spaces/s1/instances/i1')).toHaveLength(1);
    expect(alert).toHaveBeenCalledTimes(1);
    expect(textOf(renderer)).toContain('Uninstalled Groceries');
    alert.mockRestore();
  });

  it('a viewer gets no update, rebuild or uninstall', async () => {
    const withUpdate = { ...instance('i2', 's2', 'chores', 'Chores'), update: { id: 'v2', version: '1.1.0', permissions: {} } };
    mockFetchRoutes({
      'GET /api/platform/spaces': { body: { spaces: [personal, family] } },
      'GET /api/platform/spaces/s1/instances': { body: { instances: [] } },
      'GET /api/platform/spaces/s2/instances': { body: { instances: [withUpdate] } },
      'GET /api/platform/apps/app-chores': appRoute('chores', null),
    });
    renderer = await render(HomeScreen);
    expect(exists(renderer, 'apps-open-chores-badge')).toBe(false);

    await longPress(renderer, 'apps-open-chores');
    expect(exists(renderer, 'app-sheet-open')).toBe(true);
    expect(exists(renderer, 'app-sheet-info')).toBe(true);
    expect(exists(renderer, 'apps-update-chores')).toBe(false);
    expect(exists(renderer, 'app-sheet-rebuild')).toBe(false);
    expect(exists(renderer, 'app-sheet-uninstall')).toBe(false);
  });
});
