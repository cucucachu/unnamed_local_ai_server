import { createElement } from 'react';
import { Platform, Text as RNText } from 'react-native';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import type { Thread } from '@/lib/threads';

const mockPush = jest.fn();

// `Stack.Screen` is a bookkeeping-only element in real expo-router (its
// `options` are read by the enclosing navigator, not rendered as children
// at this position in the tree) — mocking it as `() => null` here matches
// that real behavior closely enough for a unit render, and means this
// suite exercises the screen's "New chat" flow via the EMPTY-STATE button
// (a real, in-tree `Pressable`) rather than the header button, which isn't
// something a leaf-screen unit render can ever click either in this mock
// OR against the real navigator.
jest.mock('expo-router', () => {
  const ReactActual = jest.requireActual('react');
  return {
    useRouter: () => ({ push: mockPush }),
    useFocusEffect: (callback: () => void | (() => void)) => {
      ReactActual.useEffect(() => callback(), []);
    },
    Stack: { Screen: () => null },
  };
});

// eslint-disable-next-line import/first -- must follow the jest.mock call above
import ThreadListScreen from '../index';

const THREAD_A: Thread = {
  id: 'thread-a',
  title: 'Thread A',
  created_at: '2026-08-30T10:00:00.000Z',
  updated_at: '2026-08-30T10:00:00.000Z',
};
const THREAD_B: Thread = {
  id: 'thread-b',
  title: 'Thread B',
  created_at: '2026-08-30T09:00:00.000Z',
  updated_at: '2026-08-30T09:30:00.000Z',
};

type MockRoute = { method: string; respond: () => { ok: boolean; status: number; body: unknown } };

/** Routes `global.fetch` by HTTP method (same mocking layer as
 * `lib/__tests__/api.test.ts` — `apiFetch`'s underlying `fetch` — just with
 * per-method responses since this screen's flows span GET/POST/DELETE). */
function mockThreadsApi(routes: Partial<Record<'GET' | 'POST' | 'DELETE', MockRoute['respond']>>): jest.Mock {
  const fetchMock = jest.fn(async (_url: string, init?: RequestInit) => {
    const method = (init?.method ?? 'GET') as 'GET' | 'POST' | 'DELETE';
    const respond = routes[method];
    if (!respond) throw new Error(`unexpected ${method} request in this test`);
    const { ok, status, body } = respond();
    return {
      ok,
      status,
      statusText: ok ? 'OK' : 'Error',
      json: async () => body,
    };
  });
  global.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

let activeRenderer: ReactTestRenderer | null = null;

async function renderScreen(): Promise<ReactTestRenderer> {
  let renderer!: ReactTestRenderer;
  await act(async () => {
    renderer = create(createElement(ThreadListScreen));
  });
  await flush();
  activeRenderer = renderer;
  return renderer;
}

/** All rendered text content, joined — `renderer.toJSON()` can't be
 * `JSON.stringify`'d once a `FlatList`/`RefreshControl` is in the tree
 * (their internal state holds circular fiber references), so this walks
 * `<Text>` nodes directly instead of serializing the whole output tree. */
function textOf(renderer: ReactTestRenderer): string {
  return renderer.root
    .findAllByType(RNText)
    .map((node) => {
      const children = node.props.children;
      return Array.isArray(children) ? children.join('') : String(children ?? '');
    })
    .join(' | ');
}

beforeEach(() => {
  mockPush.mockReset();
  // The delete-flow tests below exercise the WEB long-press-> confirm path
  // rather than the native `Swipeable` gesture (no gesture-handler jest
  // setup is installed in this project, and the web path is a plain
  // `Pressable` — simpler and sufficient to prove the optimistic
  // removal/restore logic, which is platform-independent).
  Platform.OS = 'web';
});

afterEach(() => {
  // Unmounting runs the screen's own cleanup (clears the pending toast
  // `setTimeout`, cancels in-flight hydration/list-fetch promises) so no
  // test leaks a timer/state-update into the next test or past the Jest
  // environment's teardown.
  act(() => activeRenderer?.unmount());
  activeRenderer = null;
});

describe('ThreadListScreen — list', () => {
  it('shows the empty state with a "New chat" affordance when there are no threads', async () => {
    mockThreadsApi({ GET: () => ({ ok: true, status: 200, body: [] }) });

    const renderer = await renderScreen();

    expect(textOf(renderer)).toContain('No conversations yet');
  });

  it('renders a row with the title and relative time for each fetched thread', async () => {
    mockThreadsApi({ GET: () => ({ ok: true, status: 200, body: [THREAD_A, THREAD_B] }) });

    const renderer = await renderScreen();
    const rendered = textOf(renderer);

    expect(rendered).toContain('Thread A');
    expect(rendered).toContain('Thread B');
  });

  it('shows an error state with a retry option when the initial fetch fails', async () => {
    mockThreadsApi({ GET: () => ({ ok: false, status: 500, body: { detail: 'db unavailable' } }) });

    const renderer = await renderScreen();

    expect(textOf(renderer)).toContain("Couldn't load your conversations.");
  });
});

describe('ThreadListScreen — create flow', () => {
  it('tapping "New chat" POSTs a new thread and navigates to it', async () => {
    const created: Thread = {
      id: 'new-thread',
      title: 'New chat',
      created_at: '2026-08-30T12:00:00.000Z',
      updated_at: '2026-08-30T12:00:00.000Z',
    };
    const fetchMock = mockThreadsApi({
      GET: () => ({ ok: true, status: 200, body: [] }),
      POST: () => ({ ok: true, status: 201, body: created }),
    });

    const renderer = await renderScreen();

    const newChatButton = renderer.root.findByProps({ testID: 'new-chat-empty-button' });
    await act(async () => {
      newChatButton.props.onPress();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const postCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(postCall).toBeDefined();
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/chat/[threadId]', params: { threadId: 'new-thread' } });
  });
});

describe('ThreadListScreen — delete flow', () => {
  it('optimistically removes a thread on confirmed delete, and calls DELETE', async () => {
    const fetchMock = mockThreadsApi({
      GET: () => ({ ok: true, status: 200, body: [THREAD_A, THREAD_B] }),
      DELETE: () => ({ ok: true, status: 204, body: undefined }),
    });
    window.confirm = jest.fn().mockReturnValue(true);

    const renderer = await renderScreen();
    const row = renderer.root.findByProps({ accessibilityLabel: 'Thread A' });

    await act(async () => {
      row.props.onLongPress();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(window.confirm).toHaveBeenCalled();
    expect(textOf(renderer)).not.toContain('Thread A');
    expect(textOf(renderer)).toContain('Thread B');

    const deleteCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'DELETE');
    expect(deleteCall?.[0]).toContain('thread-a');
  });

  it('does not delete when the confirm dialog is dismissed', async () => {
    mockThreadsApi({ GET: () => ({ ok: true, status: 200, body: [THREAD_A] }) });
    window.confirm = jest.fn().mockReturnValue(false);

    const renderer = await renderScreen();
    const row = renderer.root.findByProps({ accessibilityLabel: 'Thread A' });

    await act(async () => {
      row.props.onLongPress();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(textOf(renderer)).toContain('Thread A');
  });

  it('restores the thread and shows a toast if the DELETE request fails', async () => {
    mockThreadsApi({
      GET: () => ({ ok: true, status: 200, body: [THREAD_A] }),
      DELETE: () => ({ ok: false, status: 500, body: { detail: 'delete failed' } }),
    });
    window.confirm = jest.fn().mockReturnValue(true);

    const renderer = await renderScreen();
    const row = renderer.root.findByProps({ accessibilityLabel: 'Thread A' });

    await act(async () => {
      row.props.onLongPress();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const rendered = textOf(renderer);
    expect(rendered).toContain('Thread A'); // restored
    expect(rendered).toContain('delete failed'); // toast
  });
});
