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
    stopTurn: jest.fn(),
    busy: false,
    connectionState: 'open',
    hydrationState: 'done',
    retryHydration: jest.fn(),
    pendingApproval: null,
    respondToApproval: jest.fn(),
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

  /** `findAllByProps` matches both the host `View` instance and any
   * composite ancestor that also carries the prop through, so a raw
   * `.length` on a `testID`-tagged `View` over-counts by a fixed factor —
   * filtering to `type === 'View'` (the host-level match only) gives the
   * real number of rendered rows. */
  function countHostMatches(renderer: ReturnType<typeof create>, testID: string): number {
    return renderer.root
      .findAllByProps({ testID })
      .filter((instance) => (instance.type as unknown) === 'View').length;
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

    // While busy, the Send button is swapped out for the Stop button
    // entirely (see the M8-01 tests below) — it no longer renders at all.
    expect(() => renderer?.root.findByProps({ accessibilityLabel: 'Send message' })).toThrow();
  });

  describe('Stop button (M8-01)', () => {
    it('shows the Send button (not Stop) while idle', () => {
      setUseChatResult({ busy: false });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(renderer?.root.findByProps({ accessibilityLabel: 'Send message' })).toBeTruthy();
      expect(() => renderer?.root.findByProps({ testID: 'chat-stop' })).toThrow();
    });

    it('swaps the Send button for a Stop button (testID="chat-stop") while busy, calling stopTurn on press', () => {
      const stopTurn = jest.fn();
      setUseChatResult({ busy: true, stopTurn });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      const stopButton = renderer?.root.findByProps({ testID: 'chat-stop' });
      expect(stopButton).toBeTruthy();

      act(() => {
        (stopButton?.props as { onPress: () => void }).onPress();
      });
      expect(stopTurn).toHaveBeenCalledTimes(1);
    });

    it('shows a small "Stopped" caption on an assistant item with stopped: true', () => {
      setUseChatResult({
        items: [{ id: 'a-stopped', kind: 'assistant', text: 'partial reply', streaming: false, stopped: true }],
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(renderedText(renderer!)).toContain('partial reply');
      expect(renderer?.root.findByProps({ testID: 'chat-item-stopped-caption' })).toBeTruthy();
    });

    it('does NOT show a "Stopped" caption on a normal (non-stopped) assistant item', () => {
      setUseChatResult({
        items: [{ id: 'a-normal', kind: 'assistant', text: 'normal reply', streaming: false }],
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(() => renderer?.root.findByProps({ testID: 'chat-item-stopped-caption' })).toThrow();
    });
  });

  describe('ApprovalCard (M8-03)', () => {
    const singleAction = {
      toolCallId: 'call-1',
      name: 'write_file',
      category: 'file' as const,
      args: { file_path: '/x.txt', content: 'hello world' },
      description: 'Write file `/x.txt`',
    };

    it('is not rendered when pendingApproval is null', () => {
      setUseChatResult({ pendingApproval: null });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(() => renderer?.root.findByProps({ testID: 'approval-card' })).toThrow();
    });

    it('renders one row per action, the command/args, and disables the composer', () => {
      setUseChatResult({
        pendingApproval: { interruptId: 'int-1', actions: [singleAction] },
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      const card = renderer?.root.findByProps({ testID: 'approval-card' });
      expect(card).toBeTruthy();
      expect(countHostMatches(renderer!, 'approval-row')).toBe(1);
      expect(renderedText(renderer!)).toContain('/x.txt');
      expect(renderedText(renderer!)).toContain('hello world');

      // No "Approve all" for a single action.
      expect(() => renderer?.root.findByProps({ accessibilityLabel: 'Approve all' })).toThrow();

      // Composer disabled while awaiting approval.
      const input = renderer?.root.findByProps({ placeholder: 'Message…' });
      expect(input?.props.editable).toBe(false);
    });

    it('shows "Approve all" when there is more than one pending action', () => {
      setUseChatResult({
        pendingApproval: {
          interruptId: 'int-2',
          actions: [
            singleAction,
            {
              toolCallId: 'call-2',
              name: 'execute_code',
              category: 'exec' as const,
              args: { command: 'rm -rf /tmp/x' },
              description: 'Run command: `rm -rf /tmp/x`',
            },
          ],
        },
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(countHostMatches(renderer!, 'approval-row')).toBe(2);
      expect(renderer?.root.findByProps({ accessibilityLabel: 'Approve all' })).toBeTruthy();
      expect(renderedText(renderer!)).toContain('rm -rf /tmp/x');
    });

    it('Approve/Reject buttons call respondToApproval with the right decision and disable after one response', () => {
      const respondToApproval = jest.fn();
      setUseChatResult({
        pendingApproval: { interruptId: 'int-3', actions: [singleAction] },
        respondToApproval,
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      const approveButton = renderer?.root.findByProps({ accessibilityLabel: 'Approve write_file' });
      act(() => {
        (approveButton?.props as { onPress: () => void }).onPress();
      });

      expect(respondToApproval).toHaveBeenCalledWith([{ tool_call_id: 'call-1', decision: 'approve' }]);
      expect((approveButton?.props as { disabled: boolean }).disabled).toBe(true);

      const rejectButton = renderer?.root.findByProps({ accessibilityLabel: 'Reject write_file' });
      expect((rejectButton?.props as { disabled: boolean }).disabled).toBe(true);

      // A second tap (e.g. a fast double-tap race) must not send twice.
      act(() => {
        (approveButton?.props as { onPress: () => void }).onPress();
      });
      expect(respondToApproval).toHaveBeenCalledTimes(1);
    });

    it('per-row Approve/Reject on a multi-action card waits until every row has a decision', () => {
      const respondToApproval = jest.fn();
      setUseChatResult({
        pendingApproval: {
          interruptId: 'int-mixed',
          actions: [
            singleAction,
            {
              toolCallId: 'call-2',
              name: 'delete',
              category: 'file' as const,
              args: { file_path: '/y.txt' },
              description: 'Delete `/y.txt`',
            },
          ],
        },
        respondToApproval,
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      const approveWrite = renderer?.root.findByProps({ accessibilityLabel: 'Approve write_file' });
      act(() => {
        (approveWrite?.props as { onPress: () => void }).onPress();
      });
      expect(respondToApproval).not.toHaveBeenCalled();

      const rejectDelete = renderer?.root.findByProps({ accessibilityLabel: 'Reject delete' });
      act(() => {
        (rejectDelete?.props as { onPress: () => void }).onPress();
      });
      expect(respondToApproval).toHaveBeenCalledWith([
        { tool_call_id: 'call-1', decision: 'approve' },
        { tool_call_id: 'call-2', decision: 'reject' },
      ]);
    });

    it('"Approve all" sends one approve decision per pending action', () => {
      const respondToApproval = jest.fn();
      setUseChatResult({
        pendingApproval: {
          interruptId: 'int-4',
          actions: [
            singleAction,
            {
              toolCallId: 'call-2',
              name: 'delete',
              category: 'file' as const,
              args: { file_path: '/y.txt' },
              description: 'Delete `/y.txt`',
            },
          ],
        },
        respondToApproval,
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      const approveAllButton = renderer?.root.findByProps({ accessibilityLabel: 'Approve all' });
      act(() => {
        (approveAllButton?.props as { onPress: () => void }).onPress();
      });

      expect(respondToApproval).toHaveBeenCalledWith([
        { tool_call_id: 'call-1', decision: 'approve' },
        { tool_call_id: 'call-2', decision: 'approve' },
      ]);
    });

    it('renders a "rejected" chip on a tool item with status "rejected"', () => {
      setUseChatResult({
        items: [
          {
            id: 't-rejected',
            kind: 'tool',
            toolCallId: 'call-1',
            name: 'write_file',
            category: 'file',
            status: 'rejected',
            args: { file_path: '/x.txt', content: 'y' },
          },
        ],
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(renderer?.root.findByProps({ testID: 'chat-item-tool-rejected-chip' })).toBeTruthy();
      expect(renderedText(renderer!)).toContain('rejected');
    });
  });
});
