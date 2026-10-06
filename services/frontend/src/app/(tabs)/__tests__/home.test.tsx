import { Alert } from 'react-native';
import { act, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';

import { exists, flush, mockFetchRoutes, press, render, requestsTo, textOf } from '../../../../test-utils/screen';

const mockPush = jest.fn();
const mockScreenOptions = jest.fn();
jest.mock('expo-router', () => ({
  Stack: {
    Screen: ({ options }: { options: unknown }) => {
      mockScreenOptions(options);
      return null;
    },
  },
  useRouter: () => ({ push: mockPush }),
  useFocusEffect: (effect: () => void) => {
    const { useEffect } = jest.requireActual('react');
    useEffect(effect, [effect]);
  },
}));

const mockChatPageActive = jest.fn();
jest.mock('@/components/ChatPage', () => ({
  ChatPage: ({ active }: { active: boolean }) => {
    mockChatPageActive(active);
    return null;
  },
}));

let mockAttention = 0;
jest.mock('@/lib/chatAttention', () => ({ useChatAttention: () => mockAttention }));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import { gridColumns } from '@/components/AppGrid';
// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import { getCurrentPage, resetCurrentPage, showPage } from '@/lib/currentPage';
// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import HomeScreen from '../index';

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
const personal = space('s1', 'personal', 'Alice', 'owner');
const family = space('s2', 'shared', 'Family', 'editor');
const club = space('s3', 'shared', 'Book club', 'viewer');
const appRoute = (slug: string, sourcePath: string | null) => ({
  body: { id: `app-${slug}`, slug, name: slug, source_space_id: 's1', source_path: sourcePath, working_version: null },
});

function threeSpaces() {
  return mockFetchRoutes({
    'GET /api/platform/spaces': { body: { spaces: [family, personal, club, space('s4', 'shared', 'Old', 'owner', '2026-01-01T00:00:00Z')] } },
    'GET /api/platform/spaces/s1/instances': { body: { instances: [instance('i1', 's1', 'groceries', 'Groceries')] } },
    'GET /api/platform/spaces/s2/instances': { body: { instances: [instance('i2', 's2', 'chores', 'Chores')] } },
    'GET /api/platform/spaces/s3/instances': { body: { instances: [] } },
  });
}

let renderer: ReactTestRenderer | null = null;

beforeEach(() => {
  mockPush.mockReset();
  mockScreenOptions.mockReset();
  mockChatPageActive.mockReset();
  mockAttention = 0;
  resetCurrentPage();
});
afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
});

const title = () => (mockScreenOptions.mock.calls.at(-1)?.[0] as { title?: string } | undefined)?.title;
const chatActive = () => mockChatPageActive.mock.calls.at(-1)?.[0];
const selectedTab = (target: ReactTestRenderer) =>
  target.root.find((node) => node.props.accessibilityRole === 'tab' && node.props['aria-selected'] && typeof node.props.onPress === 'function').props
    .testID;
const page = (target: ReactTestRenderer, slug: string) =>
  target.root.find((node) => node.props.testID === `apps-space-${slug}` && typeof node.type !== 'string');
const tilesOn = (pageNode: ReactTestInstance) =>
  pageNode
    .findAll((node) => typeof node.props.testID === 'string' && typeof node.props.onPress === 'function' && typeof node.type !== 'string')
    .map((node) => node.props.testID)
    .filter((id, index, all) => all.indexOf(id) === index);

async function swipeTo(target: ReactTestRenderer, index: number, width = 390): Promise<void> {
  const pager = target.root.find((node) => node.props.testID === 'home-pages' && typeof node.props.onScroll === 'function');
  await act(async () => {
    pager.props.onLayout({ nativeEvent: { layout: { width, height: 700, x: 0, y: 0 } } });
  });
  await act(async () => {
    pager.props.onScroll({ nativeEvent: { contentOffset: { x: index * width, y: 0 } } });
  });
}

async function longPress(target: ReactTestRenderer, testID: string): Promise<void> {
  await act(async () => {
    target.root.find((node) => node.props.testID === testID && typeof node.props.onLongPress === 'function').props.onLongPress();
  });
  await flush();
}

describe('HomeScreen pager (M19-07)', () => {
  it('pages are Chat, then Personal, then live shared spaces by name; a cold launch opens Chat', async () => {
    threeSpaces();
    renderer = await render(HomeScreen);
    const pages = renderer.root
      .findAll((node) => typeof node.props.testID === 'string' && node.props.testID.startsWith('apps-space-') && typeof node.type !== 'string')
      .map((node) => node.props.testID);
    expect([...new Set(pages)]).toEqual(['apps-space-personal-s1', 'apps-space-shared-s3', 'apps-space-shared-s2']);
    expect(exists(renderer, 'apps-space-shared-s4')).toBe(false);
    expect(chatActive()).toBe(true);
    expect(selectedTab(renderer)).toBe('home-page-chat');
  });

  it('Files is on every page; Routines and Settings only on Personal; Add app last where you can install', async () => {
    threeSpaces();
    renderer = await render(HomeScreen);
    expect(tilesOn(page(renderer, 'personal-s1'))).toEqual([
      'home-open-files',
      'home-open-routines',
      'home-open-settings',
      'apps-open-groceries',
      'apps-add',
    ]);
    expect(tilesOn(page(renderer, 'shared-s2'))).toEqual(['home-open-files', 'apps-open-chores', 'apps-add']);
    expect(tilesOn(page(renderer, 'shared-s3'))).toEqual(['home-open-files']);
    expect(textOf(renderer)).toContain('No apps in Book club yet.');
    expect(exists(renderer, 'home-open-chat')).toBe(false);
  });

  it('swiping moves through the pages; the title, indicator and Files follow; the page is kept', async () => {
    threeSpaces();
    renderer = await render(HomeScreen);

    await swipeTo(renderer, 1);
    expect(chatActive()).toBe(false);
    expect(title()).toBe('Personal');
    expect(selectedTab(renderer)).toBe('home-space-personal-s1');

    await swipeTo(renderer, 3);
    expect(title()).toBe('Family');
    expect(getCurrentPage().page).toBe('s2');
    await act(async () => page(renderer!, 'shared-s2').find((n) => n.props.testID === 'home-open-files' && typeof n.props.onPress === 'function').props.onPress());
    expect(mockPush).toHaveBeenLastCalledWith({ pathname: '/files', params: { path: '/spaces/shared-s2' } });
    await act(async () => page(renderer!, 'shared-s2').find((n) => n.props.testID === 'apps-add' && typeof n.props.onPress === 'function').props.onPress());
    expect(mockPush).toHaveBeenLastCalledWith({ pathname: '/apps/catalog', params: { spaceId: 's2' } });

    act(() => renderer?.unmount());
    renderer = await render(HomeScreen);
    expect(title()).toBe('Family');

    await swipeTo(renderer, 0);
    expect(chatActive()).toBe(true);
  });

  it('the indicator and links jump to a page', async () => {
    threeSpaces();
    renderer = await render(HomeScreen);
    await press(renderer, 'home-space-shared-s3');
    expect(title()).toBe('Book club');
    await press(renderer, 'home-page-chat');
    expect(chatActive()).toBe(true);
    act(() => showPage('apps'));
    expect(title()).toBe('Personal');
  });

  it('the chat indicator carries the chats-needing-you count', async () => {
    mockAttention = 2;
    threeSpaces();
    renderer = await render(HomeScreen);
    expect(exists(renderer, 'home-page-chat-badge')).toBe(true);
    expect(textOf(renderer)).toContain('2');
  });

  it('has a chat icon and one dot with only Personal', async () => {
    mockFetchRoutes({
      'GET /api/platform/spaces': { body: { spaces: [personal] } },
      'GET /api/platform/spaces/s1/instances': { body: { instances: [] } },
    });
    renderer = await render(HomeScreen);
    const tabs = renderer.root.findAll((node) => node.props.accessibilityRole === 'tab' && typeof node.props.onPress === 'function');
    expect(new Set(tabs.map((t) => t.props.testID))).toEqual(new Set(['home-page-chat', 'home-space-personal-s1']));
  });
});

describe('app grid (M19-04)', () => {
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
    expect(exists(renderer, 'app-icon-glyph-add')).toBe(true);
  });

  it('tap opens the runner; an update shows a dot and is offered on long press', async () => {
    const withUpdate = { ...instance('i2', 's2', 'chores', 'Chores'), update: { id: 'v2', version: '1.1.0', permissions: {} } };
    mockFetchRoutes({
      'GET /api/platform/spaces': { body: { spaces: [family, personal] } },
      'GET /api/platform/spaces/s1/instances': { body: { instances: [] } },
      'GET /api/platform/spaces/s2/instances': { body: { instances: [withUpdate] } },
      'GET /api/platform/apps/app-chores': appRoute('chores', null),
    });
    renderer = await render(HomeScreen);
    expect(exists(renderer, 'apps-open-chores-badge')).toBe(true);
    await press(renderer, 'apps-open-chores');
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/apps/[instanceId]', params: { instanceId: 'i2' } });
    await longPress(renderer, 'apps-open-chores');
    await press(renderer, 'apps-update-chores');
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/apps/update', params: { spaceId: 's2', instanceId: 'i2' } });
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
    const withUpdate = { ...instance('i2', 's3', 'chores', 'Chores'), update: { id: 'v2', version: '1.1.0', permissions: {} } };
    mockFetchRoutes({
      'GET /api/platform/spaces': { body: { spaces: [personal, club] } },
      'GET /api/platform/spaces/s1/instances': { body: { instances: [] } },
      'GET /api/platform/spaces/s3/instances': { body: { instances: [withUpdate] } },
      'GET /api/platform/apps/app-chores': appRoute('chores', '/club/apps/chores'),
    });
    renderer = await render(HomeScreen);
    expect(exists(renderer, 'apps-open-chores-badge')).toBe(false);
    expect(textOf(renderer)).toContain('View only');

    await longPress(renderer, 'apps-open-chores');
    expect(exists(renderer, 'app-sheet-open')).toBe(true);
    expect(exists(renderer, 'app-sheet-info')).toBe(true);
    expect(exists(renderer, 'apps-update-chores')).toBe(false);
    expect(exists(renderer, 'app-sheet-rebuild')).toBe(false);
    expect(exists(renderer, 'app-sheet-uninstall')).toBe(false);
  });
});

