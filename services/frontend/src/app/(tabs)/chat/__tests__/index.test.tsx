import { createElement, type ReactElement } from 'react';
import { Platform, Text as RNText } from 'react-native';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';

import type { Thread } from '@/lib/threads';

const mockDismissTo = jest.fn();
let mockSearchParams: Record<string, string> = {};
let mockHeaderOptions: { title?: string; headerLeft?: () => ReactElement; headerRight?: () => ReactElement } = {};

jest.mock('expo-router', () => {
  const ReactActual = jest.requireActual('react');
  return {
    useRouter: () => ({ dismissTo: mockDismissTo }),
    useLocalSearchParams: () => mockSearchParams,
    useFocusEffect: (callback: () => void | (() => void)) => {
      ReactActual.useEffect(() => callback(), []);
    },
    Stack: {
      Screen: function Screen({ options }: { options: typeof mockHeaderOptions }) {
        mockHeaderOptions = options;
        return null;
      },
    },
  };
});

const mockChatViewMounts = jest.fn();
let mockChatViewProps: { threadId: string | null; onThreadCreated?: (id: string) => void; onTurnEnd?: () => void } | null =
  null;
jest.mock('@/components/ChatView', () => {
  const ReactActual = jest.requireActual('react');
  return {
    ChatView: function ChatView(props: NonNullable<typeof mockChatViewProps>) {
      mockChatViewProps = props;
      ReactActual.useEffect(() => {
        mockChatViewMounts();
      }, []);
      return null;
    },
  };
});

jest.mock('@/components/SwipeToOpen', () => ({
  SwipeToOpen: ({ children }: { children: ReactElement }) => children,
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import { openChat, resetCurrentChat } from '@/lib/currentChat';
// eslint-disable-next-line import/first
import ChatDeepLink from '../[threadId]';
// eslint-disable-next-line import/first
import ChatTabScreen from '../index';

const APPROVAL: Thread = {
  id: 'thread-approval',
  title: 'Needs you',
  created_at: '2026-08-30T08:00:00.000Z',
  updated_at: '2026-08-30T08:00:00.000Z',
  needs_approval: true,
};
const UNREAD: Thread = {
  id: 'thread-unread',
  title: 'Morning brief',
  created_at: '2026-08-30T07:00:00.000Z',
  updated_at: '2026-08-30T07:00:00.000Z',
  routine_id: 'routine-1',
  unread: true,
};
const RECENT: Thread = {
  id: 'thread-recent',
  title: 'Trip planning',
  created_at: '2026-08-30T10:00:00.000Z',
  updated_at: '2026-08-30T10:00:00.000Z',
};
const SERVER_ORDER = [APPROVAL, UNREAD, RECENT];

type Responder = (url: string, body: unknown) => { ok: boolean; status: number; body: unknown };

function mockThreadsApi(routes: Partial<Record<'GET' | 'PATCH' | 'DELETE' | 'POST', Responder>>): jest.Mock {
  const fetchMock = jest.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method ?? 'GET') as keyof typeof routes;
    const respond = routes[method];
    if (!respond) throw new Error(`unexpected ${method} ${url} in this test`);
    const { ok, status, body } = respond(url, init?.body ? JSON.parse(String(init.body)) : undefined);
    return { ok, status, statusText: ok ? 'OK' : 'Error', json: async () => body };
  });
  global.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

const listOk = (threads: Thread[] = SERVER_ORDER) => ({ GET: () => ({ ok: true, status: 200, body: threads }) });

async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

let activeRenderer: ReactTestRenderer | null = null;

async function renderTab(): Promise<ReactTestRenderer> {
  let renderer!: ReactTestRenderer;
  await act(async () => {
    renderer = create(createElement(ChatTabScreen));
  });
  await flush();
  activeRenderer = renderer;
  return renderer;
}

function unmountTab(): void {
  act(() => activeRenderer?.unmount());
  activeRenderer = null;
}

function hostByTestId(renderer: ReactTestRenderer, testID: string): ReactTestInstance[] {
  return renderer.root.findAll((node) => typeof node.type === 'string' && node.props.testID === testID);
}

function press(renderer: ReactTestRenderer, testID: string, index = 0, handler = 'onPress'): void {
  const node = renderer.root.findAll(
    (candidate) => candidate.props.testID === testID && typeof candidate.props[handler] === 'function',
  )[index];
  if (!node) throw new Error(`no pressable ${testID}`);
  act(() => {
    node.props[handler]();
  });
}

function rowTitles(renderer: ReactTestRenderer): string[] {
  return hostByTestId(renderer, 'thread-row').map((row) =>
    row
      .findAllByType(RNText)
      .map((node) => node.props.children)
      .filter((child) => typeof child === 'string')[0],
  );
}

function headerButton(render: (() => ReactElement) | undefined, testID: string): { onPress: () => void } {
  const element = render?.() as ReactElement<{ testID: string; onPress: () => void }> | undefined;
  if (element?.props.testID !== testID) throw new Error(`no header button ${testID}`);
  return element.props;
}

async function openDrawer(): Promise<void> {
  act(() => headerButton(mockHeaderOptions.headerLeft, 'chat-history-button').onPress());
  await flush();
}

function pressNewChat(): void {
  act(() => headerButton(mockHeaderOptions.headerRight, 'new-chat-header-button').onPress());
}

beforeEach(() => {
  act(() => resetCurrentChat());
  mockChatViewProps = null;
  mockChatViewMounts.mockClear();
  mockDismissTo.mockClear();
  mockHeaderOptions = {};
  Platform.OS = 'web';
  (window as unknown as { confirm: () => boolean }).confirm = jest.fn(() => true);
});

afterEach(() => {
  unmountTab();
  jest.restoreAllMocks();
});

describe('Chat tab (M19-02)', () => {
  it('a cold launch shows a new empty chat', async () => {
    mockThreadsApi(listOk());
    await renderTab();
    expect(mockChatViewProps?.threadId).toBeNull();
    expect(mockHeaderOptions.title).toBe('New chat');
  });

  it('the drawer lists chats in the server order with their markers, and tapping one opens it', async () => {
    mockThreadsApi(listOk());
    const renderer = await renderTab();
    expect(hostByTestId(renderer, 'chat-drawer')).toHaveLength(0);

    await openDrawer();
    expect(hostByTestId(renderer, 'chat-drawer')).toHaveLength(1);
    expect(rowTitles(renderer)).toEqual(['Needs you', 'Morning brief', 'Trip planning']);
    expect(hostByTestId(renderer, 'thread-needs-approval-thread-approval')).toHaveLength(1);
    expect(hostByTestId(renderer, 'thread-unread-thread-unread')).toHaveLength(1);
    expect(hostByTestId(renderer, 'thread-routine-thread-unread')).toHaveLength(1);

    press(renderer, 'thread-row', 2);
    expect(hostByTestId(renderer, 'chat-drawer')).toHaveLength(0);
    expect(mockChatViewProps?.threadId).toBe('thread-recent');
    expect(mockHeaderOptions.title).toBe('Trip planning');
  });

  it('switching tabs and back returns to the same chat', async () => {
    mockThreadsApi(listOk());
    await renderTab();
    act(() => openChat('thread-recent'));
    unmountTab();

    await renderTab();
    expect(mockChatViewProps?.threadId).toBe('thread-recent');
  });

  it('+ gives a new chat', async () => {
    mockThreadsApi(listOk());
    await renderTab();
    act(() => openChat('thread-recent'));
    const mountsBefore = mockChatViewMounts.mock.calls.length;

    pressNewChat();
    expect(mockChatViewProps?.threadId).toBeNull();
    expect(mockChatViewMounts.mock.calls.length).toBe(mountsBefore + 1);
  });

  it('a new chat that gets its thread on the first send stays mounted', async () => {
    mockThreadsApi(listOk());
    await renderTab();
    const mountsBefore = mockChatViewMounts.mock.calls.length;

    act(() => mockChatViewProps?.onThreadCreated?.('thread-recent'));
    expect(mockChatViewProps?.threadId).toBe('thread-recent');
    expect(mockChatViewMounts.mock.calls.length).toBe(mountsBefore);
  });

  it('search and the routine filter narrow the list', async () => {
    mockThreadsApi(listOk());
    const renderer = await renderTab();
    await openDrawer();

    const search = renderer.root.findByProps({ testID: 'chat-drawer-search' });
    act(() => search.props.onChangeText('trip'));
    expect(rowTitles(renderer)).toEqual(['Trip planning']);
    act(() => search.props.onChangeText(''));

    press(renderer, 'thread-filter-routine-runs');
    expect(rowTitles(renderer)).toEqual(['Needs you', 'Trip planning']);
  });

  it('long press renames a chat', async () => {
    const fetchMock = mockThreadsApi({
      ...listOk(),
      PATCH: (_url, body) => ({ ok: true, status: 200, body: { ...RECENT, title: (body as { title: string }).title } }),
    });
    const renderer = await renderTab();
    await openDrawer();

    press(renderer, 'thread-row', 2, 'onLongPress');
    press(renderer, 'thread-action-rename');
    act(() => renderer.root.findByProps({ testID: 'prompt-modal-input' }).props.onChangeText('Japan trip'));
    press(renderer, 'prompt-modal-submit');
    await flush();

    expect(rowTitles(renderer)).toEqual(['Needs you', 'Morning brief', 'Japan trip']);
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH');
    expect(String(patch?.[0])).toContain('/api/threads/thread-recent');
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ title: 'Japan trip' });
  });

  it('deleting the open chat starts a new one', async () => {
    const fetchMock = mockThreadsApi({ ...listOk(), DELETE: () => ({ ok: true, status: 204, body: undefined }) });
    const renderer = await renderTab();
    act(() => openChat('thread-recent'));
    await openDrawer();

    press(renderer, 'thread-row', 2, 'onLongPress');
    press(renderer, 'thread-action-delete');
    await flush();

    expect(rowTitles(renderer)).toEqual(['Needs you', 'Morning brief']);
    expect(mockChatViewProps?.threadId).toBeNull();
    expect(fetchMock.mock.calls.some(([url, init]) => init?.method === 'DELETE' && String(url).includes('thread-recent'))).toBe(
      true,
    );
  });

  it('a failed delete puts the chat back', async () => {
    mockThreadsApi({ ...listOk(), DELETE: () => ({ ok: false, status: 500, body: { detail: 'nope' } }) });
    const renderer = await renderTab();
    await openDrawer();

    press(renderer, 'thread-row', 1, 'onLongPress');
    press(renderer, 'thread-action-delete');
    await flush();

    expect(rowTitles(renderer)).toEqual(['Needs you', 'Morning brief', 'Trip planning']);
  });

  it('a /chat/<id> deep link opens that chat in the Chat tab', async () => {
    mockThreadsApi(listOk());
    mockSearchParams = { threadId: 'thread-unread' };
    act(() => {
      create(createElement(ChatDeepLink));
    });
    expect(mockDismissTo).toHaveBeenCalledWith('/chat');

    await renderTab();
    expect(mockChatViewProps?.threadId).toBe('thread-unread');
    expect(mockHeaderOptions.title).toBe('Morning brief');
  });
});
