import { createElement } from 'react';
import { Animated, BackHandler, Platform, Text as RNText } from 'react-native';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';

import type { Thread } from '@/lib/threads';

const mockDismissTo = jest.fn();
let mockSearchParams: Record<string, string> = {};

jest.mock('expo-router', () => {
  const ReactActual = jest.requireActual('react');
  return {
    useRouter: () => ({ dismissTo: mockDismissTo }),
    useLocalSearchParams: () => mockSearchParams,
    useFocusEffect: (callback: () => void | (() => void)) => {
      ReactActual.useEffect(() => callback(), []);
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

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import { openChat, resetCurrentChat } from '@/lib/currentChat';
// eslint-disable-next-line import/first
import { getCurrentPage } from '@/lib/currentPage';
// eslint-disable-next-line import/first
import ChatDeepLink from '../../src/app/(tabs)/chat/[threadId]';
// eslint-disable-next-line import/first
import { ChatPage, type PagedHistory } from '../ChatPage';

function ChatTabScreen() {
  return createElement(ChatPage, { width: 390 });
}

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

function headerTitle(renderer: ReactTestRenderer | null = activeRenderer): string | undefined {
  const node = renderer?.root.findAll((n) => n.type === RNText && n.props.accessibilityRole === 'header')[0];
  return node ? [node.props.children].flat().join('') : undefined;
}

async function openDrawer(): Promise<void> {
  press(activeRenderer!, 'chat-history-button');
  await flush();
}

async function pressNewChat(): Promise<void> {
  await openDrawer();
  press(activeRenderer!, 'chat-drawer-new-chat');
}

beforeEach(() => {
  act(() => resetCurrentChat());
  mockChatViewProps = null;
  mockChatViewMounts.mockClear();
  mockDismissTo.mockClear();
  Platform.OS = 'web';
  (window as unknown as { confirm: () => boolean }).confirm = jest.fn(() => true);
});

afterEach(() => {
  unmountTab();
  jest.restoreAllMocks();
});

function textOfNode(node: ReactTestInstance | undefined): string {
  return (node?.findAllByType(RNText) ?? []).map((t) => [t.props.children].flat().join('')).join(' ');
}

describe('Chat page (M19-02, M19-07)', () => {
  it('a cold launch shows a new empty chat', async () => {
    mockThreadsApi(listOk());
    await renderTab();
    expect(mockChatViewProps?.threadId).toBeNull();
    expect(headerTitle()).toBe('New chat');
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
    expect(headerTitle()).toBe('Trip planning');
  });

  it('switching tabs and back returns to the same chat', async () => {
    mockThreadsApi(listOk());
    await renderTab();
    act(() => openChat('thread-recent'));
    unmountTab();

    await renderTab();
    expect(mockChatViewProps?.threadId).toBe('thread-recent');
  });

  it("the drawer's New chat button gives a new chat; the header has no +", async () => {
    mockThreadsApi(listOk());
    await renderTab();
    act(() => openChat('thread-recent'));
    const mountsBefore = mockChatViewMounts.mock.calls.length;

    await pressNewChat();
    expect(mockChatViewProps?.threadId).toBeNull();
    expect(mockChatViewMounts.mock.calls.length).toBe(mountsBefore + 1);
    expect(hostByTestId(activeRenderer!, 'chat-drawer')).toHaveLength(0);
    expect(hostByTestId(activeRenderer!, 'new-chat-header-button')).toHaveLength(0);
  });

  it('the drawer shows five chats and More for the rest; a search looks through them all (M19-07)', async () => {
    const many: Thread[] = Array.from({ length: 8 }, (_, i) => ({
      id: `thread-${i}`,
      title: `Chat ${i}`,
      created_at: '2026-08-30T08:00:00.000Z',
      updated_at: '2026-08-30T08:00:00.000Z',
    }));
    mockThreadsApi(listOk(many));
    const renderer = await renderTab();
    await openDrawer();
    expect(rowTitles(renderer)).toEqual(['Chat 0', 'Chat 1', 'Chat 2', 'Chat 3', 'Chat 4']);
    expect(textOfNode(hostByTestId(renderer, 'chat-drawer-more')[0])).toContain('More (3)');

    act(() => hostByTestId(renderer, 'chat-drawer-search')[0].props.onChangeText('Chat 7'));
    expect(rowTitles(renderer)).toEqual(['Chat 7']);
    act(() => hostByTestId(renderer, 'chat-drawer-search')[0].props.onChangeText(''));

    press(renderer, 'chat-drawer-more');
    expect(rowTitles(renderer)).toHaveLength(8);
    expect(hostByTestId(renderer, 'chat-drawer-more')).toHaveLength(0);

    press(renderer, 'chat-drawer-close');
    await openDrawer();
    expect(rowTitles(renderer)).toHaveLength(5);
  });

  it("on a phone the history is the page beside the chat, open while the pager shows it (M19-08)", async () => {
    const fetchMock = mockThreadsApi(listOk());
    const paged: PagedHistory = { width: 330, scrollX: new Animated.Value(330), open: jest.fn(), close: jest.fn() };
    const back: { handler: (() => boolean) | null } = { handler: null };
    jest.spyOn(BackHandler, 'addEventListener').mockImplementation((_event, handler) => {
      back.handler = handler as () => boolean;
      return { remove: () => (back.handler = null) };
    });
    let renderer!: ReactTestRenderer;
    await act(async () => {
      renderer = create(createElement(ChatPage, { width: 390, paged }));
    });
    activeRenderer = renderer;
    await flush();
    expect(hostByTestId(renderer, 'chat-drawer')).toHaveLength(1);
    const backdrop = () => renderer.root.findAll((n) => n.props.pointerEvents !== undefined && n.findAll((c) => c.props.testID === 'chat-drawer-backdrop').length > 0)[0];
    expect(backdrop().props.pointerEvents).toBe('none');

    press(renderer, 'chat-history-button');
    expect(paged.open).toHaveBeenCalled();

    const loads = () => fetchMock.mock.calls.filter(([, init]) => (init?.method ?? 'GET') === 'GET').length;
    const before = loads();
    act(() => paged.scrollX.setValue(0));
    await flush();
    expect(loads()).toBe(before + 1);
    expect(backdrop().props.pointerEvents).toBe('auto');
    expect(back.handler?.()).toBe(true);
    expect(paged.close).toHaveBeenCalledTimes(1);

    press(renderer, 'thread-row', 2);
    expect(paged.close).toHaveBeenCalledTimes(2);
    expect(mockChatViewProps?.threadId).toBe('thread-recent');

    act(() => paged.scrollX.setValue(330));
    expect(backdrop().props.pointerEvents).toBe('none');
    expect(back.handler).toBeNull();
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

  it('a /chat/<id> deep link opens that chat on the Chat page', async () => {
    mockThreadsApi(listOk());
    mockSearchParams = { threadId: 'thread-unread' };
    act(() => {
      create(createElement(ChatDeepLink));
    });
    expect(mockDismissTo).toHaveBeenCalledWith('/');
    expect(getCurrentPage().page).toBe('chat');

    await renderTab();
    expect(mockChatViewProps?.threadId).toBe('thread-unread');
    expect(headerTitle()).toBe('Morning brief');
  });
});
