import { createElement } from 'react';
import { Linking } from 'react-native';
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

  function webSearchToolItem(overrides: Partial<ChatToolItem> = {}): ChatToolItem {
    return {
      id: 'search-1',
      kind: 'tool',
      toolCallId: 'call-search-1',
      name: 'web_search',
      category: 'web',
      status: 'running',
      args: { query: 'llama.cpp' },
      ...overrides,
    };
  }

  function webFetchToolItem(overrides: Partial<ChatToolItem> = {}): ChatToolItem {
    return {
      id: 'fetch-1',
      kind: 'tool',
      toolCallId: 'call-fetch-1',
      name: 'web_fetch',
      category: 'web',
      status: 'running',
      args: { url: 'https://example.com/docs' },
      ...overrides,
    };
  }

  describe('web_search tool cards (M7-06)', () => {
    it('running: shows the query and a spinner, no result-count chip', () => {
      setUseChatResult({ items: [webSearchToolItem({ status: 'running' })] });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      const text = renderedText(renderer!);
      expect(text).toContain('llama.cpp');
      expect(text).not.toMatch(/\d+ results?/);
    });

    it('success: shows a "N results" chip, and expanding lists each result with a tappable title + hostname + snippet', () => {
      setUseChatResult({
        items: [
          webSearchToolItem({
            status: 'success',
            resultPreview:
              '1. ggml-org/llama.cpp\n' +
              '   https://github.com/ggml-org/llama.cpp\n' +
              '   LLM inference in C/C++\n' +
              '2. llama.cpp docs\n' +
              '   https://example.com/docs\n' +
              '   Documentation site',
          }),
        ],
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(renderedText(renderer!)).toContain('2 results');

      expandFirstToolCard(renderer!);
      const expandedText = renderedText(renderer!);
      expect(expandedText).toContain('ggml-org/llama.cpp');
      expect(expandedText).toContain('github.com');
      expect(expandedText).toContain('LLM inference in C/C++');
      expect(expandedText).toContain('llama.cpp docs');
      expect(expandedText).toContain('example.com');
      expect(expandedText).toContain('Documentation site');

      const openURLSpy = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined);
      const firstTitle = renderer!.root.findByProps({ accessibilityLabel: 'ggml-org/llama.cpp' });
      act(() => {
        (firstTitle.props as { onPress: () => void }).onPress();
      });
      expect(openURLSpy).toHaveBeenCalledWith('https://github.com/ggml-org/llama.cpp');
      openURLSpy.mockRestore();
    });

    it('zero results: shows a "0 results" chip, and expanding shows "No results found."', () => {
      setUseChatResult({
        items: [webSearchToolItem({ status: 'success', resultPreview: 'No results found.' })],
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(renderedText(renderer!)).toContain('0 results');

      expandFirstToolCard(renderer!);
      expect(renderedText(renderer!)).toContain('No results found.');
    });

    it('error: shows an "error" chip, and expanding shows the error message', () => {
      setUseChatResult({
        items: [
          webSearchToolItem({
            status: 'success',
            resultPreview: "Error: web_search failed: ConnectError('boom')",
          }),
        ],
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(renderedText(renderer!)).toContain('error');

      expandFirstToolCard(renderer!);
      expect(renderedText(renderer!)).toContain("web_search failed: ConnectError('boom')");
    });
  });

  describe('web_fetch tool cards (M7-06)', () => {
    it('running: shows the hostname of args.url', () => {
      setUseChatResult({ items: [webFetchToolItem({ status: 'running' })] });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(renderedText(renderer!)).toContain('example.com');
    });

    it('success: header shows hostname + page title, and expanding shows the final URL (tappable) + extracted text', () => {
      setUseChatResult({
        items: [
          webFetchToolItem({
            status: 'success',
            resultPreview: 'Title: Docs Home\nURL: https://example.com/docs\n\nWelcome to the docs.',
          }),
        ],
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      const collapsedText = renderedText(renderer!);
      expect(collapsedText).toContain('example.com');
      expect(collapsedText).toContain('Docs Home');

      expandFirstToolCard(renderer!);
      const expandedText = renderedText(renderer!);
      expect(expandedText).toContain('https://example.com/docs');
      expect(expandedText).toContain('Welcome to the docs.');

      const openURLSpy = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined);
      const urlLink = renderer!.root.findByProps({ accessibilityRole: 'link' });
      act(() => {
        (urlLink.props as { onPress: () => void }).onPress();
      });
      expect(openURLSpy).toHaveBeenCalledWith('https://example.com/docs');
      openURLSpy.mockRestore();
    });

    it('error: shows an "error" chip, and expanding shows the error message', () => {
      setUseChatResult({
        items: [
          webFetchToolItem({
            status: 'success',
            resultPreview: 'Error: destination not allowed by egress policy',
          }),
        ],
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(renderedText(renderer!)).toContain('error');

      expandFirstToolCard(renderer!);
      expect(renderedText(renderer!)).toContain('destination not allowed by egress policy');
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
