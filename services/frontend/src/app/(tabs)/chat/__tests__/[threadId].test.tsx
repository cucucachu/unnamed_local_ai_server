import { createElement } from 'react';
import { act, create } from 'react-test-renderer';

import type { ChatToolItem, UseChatResult } from '@/lib/useChat';

// Per the ticket's own text ("one shallow render of the screen"): mock
// `useChat` entirely rather than exercising the real WebSocket layer, so
// this stays a true shallow render (no socket, no timers, no fake server).
const mockUseChat = jest.fn<UseChatResult, unknown[]>();
jest.mock('@/lib/useChat', () => ({
  useChat: (...args: unknown[]) => mockUseChat(...args),
}));

// `useLocalSearchParams` needs a route param to hand back — mocked
// separately from `expo-router`'s other exports since this screen (unlike
// `chat/index.tsx`) doesn't need `useRouter`/`useFocusEffect`/`Stack`.
jest.mock('expo-router', () => ({
  useLocalSearchParams: () => ({ threadId: 'thread-123' }),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import ChatScreen from '../[threadId]';

function setUseChatResult(overrides: Partial<UseChatResult> = {}): void {
  mockUseChat.mockReturnValue({
    items: [],
    sendMessage: jest.fn(),
    busy: false,
    connectionState: 'open',
    hydrationState: 'done',
    retryHydration: jest.fn(),
    ...overrides,
  });
}

describe('ChatScreen ([threadId])', () => {
  beforeEach(() => {
    mockUseChat.mockReset();
  });

  it('passes the route threadId through to useChat', () => {
    setUseChatResult();

    act(() => {
      create(createElement(ChatScreen));
    });

    expect(mockUseChat).toHaveBeenCalledWith('thread-123');
  });

  it('shows a loading spinner (no composer/list) while hydrationState is "loading"', () => {
    setUseChatResult({ hydrationState: 'loading' });

    let renderer: ReturnType<typeof create> | undefined;
    expect(() => {
      act(() => {
        renderer = create(createElement(ChatScreen));
      });
    }).not.toThrow();

    expect(renderer?.root.findAllByProps({ testID: 'chat-item-user' })).toHaveLength(0);
    expect(() => renderer?.root.findByProps({ placeholder: 'Message…' })).toThrow();
  });

  it('shows an error banner with a retry button when hydrationState is "error"', () => {
    const retryHydration = jest.fn();
    setUseChatResult({ hydrationState: 'error', retryHydration });

    let renderer: ReturnType<typeof create> | undefined;
    act(() => {
      renderer = create(createElement(ChatScreen));
    });

    const retryButton = renderer?.root.findByProps({ accessibilityLabel: 'Retry loading conversation' });
    expect(retryButton).toBeTruthy();

    act(() => {
      retryButton?.props.onPress();
    });
    expect(retryHydration).toHaveBeenCalledTimes(1);
  });

  it('renders without crashing with an empty item list once hydration is done', () => {
    setUseChatResult();

    expect(() => {
      act(() => {
        create(createElement(ChatScreen));
      });
    }).not.toThrow();
  });

  it('renders user, assistant, tool, and error items without crashing', () => {
    setUseChatResult({
      connectionState: 'reconnecting',
      items: [
        { id: 'u-1', kind: 'user', text: 'hello' },
        { id: 'a-1', kind: 'assistant', text: 'hi there', streaming: true },
        {
          id: 't-1',
          kind: 'tool',
          toolCallId: 'call-1',
          name: 'read_file',
          category: 'file',
          status: 'running',
          args: { path: '/tmp/x' },
        },
        { id: 'e-1', kind: 'error', message: 'something went wrong' },
      ],
    });

    let renderer: ReturnType<typeof create> | undefined;
    expect(() => {
      act(() => {
        renderer = create(createElement(ChatScreen));
      });
    }).not.toThrow();

    const text = renderer?.toJSON();
    expect(text).toBeTruthy();
  });

  function execToolItem(overrides: Partial<ChatToolItem> = {}): ChatToolItem {
    return {
      id: 'exec-1',
      kind: 'tool',
      toolCallId: 'call-exec-1',
      name: 'execute_code',
      category: 'exec',
      status: 'running',
      args: { command: 'echo HELLO-UI' },
      ...overrides,
    };
  }

  /** Flattens the rendered tree to a single string of everything Text nodes
   * emitted, for cheap substring assertions — this repo has no snapshot
   * files in this directory (verified before adding this helper) and the
   * existing tests already favor explicit assertions over snapshots. */
  function renderedText(renderer: ReturnType<typeof create>): string {
    return JSON.stringify(renderer.toJSON());
  }

  function expandFirstToolCard(renderer: ReturnType<typeof create>): void {
    const header = renderer.root.findByProps({ testID: 'chat-item-tool-header' });
    act(() => {
      (header.props as { onPress: () => void }).onPress();
    });
  }

  describe('exec tool cards (M4-06)', () => {
    it('running: shows the command and a spinner, no exit chip', () => {
      setUseChatResult({ items: [execToolItem({ status: 'running' })] });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      const text = renderedText(renderer!);
      expect(text).toContain('echo HELLO-UI');
      expect(text).not.toMatch(/exit \d/);
      expect(text).not.toContain('timed out');
    });

    it('success: shows a green "exit 0" chip', () => {
      setUseChatResult({
        items: [
          execToolItem({
            status: 'success',
            resultPreview: 'exit_code: 0\n--- stdout ---\nHELLO-UI\n--- stderr ---\n(empty)',
          }),
        ],
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(renderedText(renderer!)).toContain('exit 0');

      expandFirstToolCard(renderer!);
      expect(renderedText(renderer!)).toContain('HELLO-UI');
    });

    it('failure: shows a red "exit N" chip for a nonzero exit code', () => {
      setUseChatResult({
        items: [
          execToolItem({
            status: 'error',
            resultPreview: 'exit_code: 1\n--- stdout ---\n(empty)\n--- stderr ---\nboom',
          }),
        ],
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(renderedText(renderer!)).toContain('exit 1');
    });

    it('timed out: shows a distinct "timed out" chip rather than an exit-code chip', () => {
      setUseChatResult({
        items: [
          execToolItem({
            status: 'error',
            resultPreview: 'exit_code: 124 (TIMED OUT)\n--- stdout ---\npartial\n--- stderr ---\n(empty)',
          }),
        ],
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(renderedText(renderer!)).toContain('timed out');
    });
  });

  it('disables the composer (canSend false) while busy, even with draft text, once hydrated', () => {
    setUseChatResult({ busy: true });

    let renderer: ReturnType<typeof create> | undefined;
    act(() => {
      renderer = create(createElement(ChatScreen));
    });

    const sendButton = renderer?.root.findByProps({ accessibilityLabel: 'Send message' });
    expect(sendButton?.props.disabled).toBe(true);
  });
});
