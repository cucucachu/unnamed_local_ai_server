import { act, type ReactTestRenderer } from 'react-test-renderer';

import { exists, mockFetchRoutes, press, render, requestsTo, textOf } from '../../../../test-utils/screen';

const mockPush = jest.fn();
const mockReplace = jest.fn();
const mockParams: { current: Record<string, string> } = { current: {} };
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, replace: mockReplace }),
  useLocalSearchParams: () => mockParams.current,
}));

/* eslint-disable import/first -- must follow the jest.mock call above */
import CatalogScreen from '../apps/catalog';
import InstallScreen from '../apps/install';
import UpdateScreen from '../apps/update';
/* eslint-enable import/first */

const family = {
  id: 's2',
  slug: 'family',
  name: 'Family',
  kind: 'shared' as const,
  gid: 3000,
  owner_user_id: null,
  role: 'editor',
  created_at: '',
  archived_at: null,
};
const version = {
  id: 'v1',
  version: '1.0.0',
  kind: 'published' as const,
  commit: null,
  manifest: { name: 'Hello', homeai: { description: 'Says hello.', icon: 'happy-outline', permissions: {} } },
  bundle_path: 'app-bundles/a1/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/app.js',
  created_at: '',
  published_at: '',
};
const entry = {
  app: { id: 'a1', slug: 'hello', name: 'Hello', version: '1.0.0', icon: 'happy-outline' },
  version,
  installed: false,
  instance_id: null,
};

let renderer: ReactTestRenderer | null = null;
beforeEach(() => {
  mockPush.mockReset();
  mockReplace.mockReset();
  mockParams.current = {};
});
afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
});

describe('CatalogScreen', () => {
  it('lists catalog entries and opens the install sheet', async () => {
    mockFetchRoutes({
      'GET /api/platform/spaces': { body: { spaces: [family] } },
      'GET /api/platform/spaces/s2/catalog': { body: { entries: [entry] } },
    });
    renderer = await render(CatalogScreen);
    expect(textOf(renderer)).toContain('Hello');
    await press(renderer, 'catalog-app-hello');
    expect(mockPush).toHaveBeenCalledWith({
      pathname: '/apps/install',
      params: { spaceId: 's2', appId: 'a1', versionId: 'v1' },
    });
  });
});

describe('InstallScreen', () => {
  it('shows permissions and installs the pinned version', async () => {
    mockParams.current = { spaceId: 's2', appId: 'a1', versionId: 'v1' };
    const fetchMock = mockFetchRoutes({
      'GET /api/platform/spaces': { body: { spaces: [family] } },
      'GET /api/platform/spaces/s2/catalog': { body: { entries: [entry] } },
      'POST /api/platform/spaces/s2/instances': {
        body: { id: 'i9', app_id: 'a1', space_id: 's2', tracks: 'v1', app: entry.app, update: null },
      },
    });
    renderer = await render(InstallScreen);
    expect(textOf(renderer)).toContain("doesn't request extra permissions");
    await press(renderer, 'install-confirm');
    expect(requestsTo(fetchMock, 'POST', '/spaces/s2/instances')).toEqual([
      { app_id: 'a1', tracks: 'v1', granted_permissions: {} },
    ]);
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/apps/[instanceId]', params: { instanceId: 'i9' } });
  });
});

describe('UpdateScreen', () => {
  it('applies a published update', async () => {
    mockParams.current = { spaceId: 's2', instanceId: 'i2' };
    const inst = {
      id: 'i2',
      app_id: 'a1',
      space_id: 's2',
      tracks: 'v1',
      installed_by: null,
      granted_permissions: {},
      created_at: '',
      app: entry.app,
      update: { id: 'v2', version: '1.1.0', permissions: {} },
    };
    const fetchMock = mockFetchRoutes({
      'GET /api/platform/spaces': { body: { spaces: [family] } },
      'GET /api/platform/spaces/s2/instances': { body: { instances: [inst] } },
      'POST /api/platform/spaces/s2/instances/i2/update': {
        body: { instance: { ...inst, tracks: 'v2', update: null }, migration: { status: 'applied' } },
      },
    });
    renderer = await render(UpdateScreen);
    expect(textOf(renderer)).toContain('Installed 1.0.0 → 1.1.0');
    await press(renderer, 'update-confirm');
    expect(requestsTo(fetchMock, 'POST', '/spaces/s2/instances/i2/update')).toEqual([
      { version_id: 'v2', granted_permissions: {} },
    ]);
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/apps/[instanceId]', params: { instanceId: 'i2' } });
  });
});
