import { createElement } from 'react';
import { Linking } from 'react-native';
import { act, create } from 'react-test-renderer';

import { groupItemsIntoTurns } from '@/lib/chatTurns';
import type { EditMode } from '@/lib/chatSocket';
import type { StartListeningOptions } from '@/lib/speech';
import type { ChatToolItem, ChatUserItem, UseChatResult } from '@/lib/useChat';

// Per the ticket's own text ("one shallow render of the screen"): mock
// `useChat` entirely rather than exercising the real WebSocket layer, so
// this stays a true shallow render (no socket, no timers, no fake server).
const mockUseChat = jest.fn<UseChatResult, unknown[]>();
jest.mock('@/lib/useChat', () => ({
  useChat: (...args: unknown[]) => mockUseChat(...args),
}));

// `useLocalSearchParams` needs a route param to hand back. `useRouter`
// is mocked for M9-03's `file:` → Files-tab push.
const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useLocalSearchParams: () => ({ threadId: 'thread-123' }),
  useRouter: () => ({ push: mockPush }),
}));

const mockCopyToClipboard = jest.fn().mockResolvedValue(undefined);
jest.mock('@/lib/clipboard', () => ({
  copyToClipboard: (...args: unknown[]) => mockCopyToClipboard(...args),
}));

let mockEditModeDefault: EditMode = 'truncate';
jest.mock('@/components/SettingsProvider', () => ({
  useSettings: () => ({
    settings: {
      hitl_enabled: true,
      thinking_enabled: false,
      edit_mode_default: mockEditModeDefault,
    },
    loading: false,
    updateSettings: jest.fn(),
  }),
}));

const mockIsSpeechSupported = jest.fn(() => false);
const mockStartListening = jest.fn((_options: StartListeningOptions) => () => {});
const mockStopListening = jest.fn();
jest.mock('@/lib/speech', () => ({
  isSpeechSupported: () => mockIsSpeechSupported(),
  startListening: (options: StartListeningOptions) => mockStartListening(options),
  stopListening: () => mockStopListening(),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import ChatScreen from '../[threadId]';

function setUseChatResult(overrides: Partial<UseChatResult> = {}): void {
  const items = overrides.items ?? [];
  const turns = overrides.turns ?? groupItemsIntoTurns(items);
  mockUseChat.mockReturnValue({
    sendMessage: jest.fn(),
    stopTurn: jest.fn(),
    busy: false,
    connectionState: 'open',
    hydrationState: 'done',
    retryHydration: jest.fn(),
    pendingApproval: null,
    respondToApproval: jest.fn(),
    branches: [],
    switchBranch: jest.fn(),
    ...overrides,
    items,
    turns,
  });
}

describe('ChatScreen ([threadId])', () => {
  beforeEach(() => {
    mockUseChat.mockReset();
    mockCopyToClipboard.mockClear();
    mockPush.mockReset();
    mockEditModeDefault = 'truncate';
    mockIsSpeechSupported.mockReset();
    mockIsSpeechSupported.mockReturnValue(false);
    mockStartListening.mockReset();
    mockStartListening.mockImplementation(() => () => {});
    mockStopListening.mockReset();
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

  function expandActivityPanel(renderer: ReturnType<typeof create>): void {
    const header = renderer.root.findByProps({ testID: 'turn-activity-header' });
    act(() => {
      (header.props as { onPress: () => void }).onPress();
    });
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
      expandActivityPanel(renderer!);

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
      expandActivityPanel(renderer!);

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
      expandActivityPanel(renderer!);

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
      expandActivityPanel(renderer!);

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
      expandActivityPanel(renderer!);

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
      expandActivityPanel(renderer!);

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
      expandActivityPanel(renderer!);

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
      expandActivityPanel(renderer!);

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
      expandActivityPanel(renderer!);
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

      expandActivityPanel(renderer!);
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
      expandActivityPanel(renderer!);

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

      expect(renderer?.root.findByProps({ testID: 'chat-item-stopped-caption' })).toBeTruthy();
      expect(renderedText(renderer!)).toContain('Stopped');
      expandActivityPanel(renderer!);
      expect(renderedText(renderer!)).toContain('partial reply');
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

    it('renders finished assistant text as markdown and keeps streaming text plain', () => {
      setUseChatResult({
        items: [{ id: 'a-stream', kind: 'assistant', text: '# Still streaming', streaming: true }],
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(renderer?.root.findByProps({ testID: 'turn-activity-status' }).props.children).toBe('Writing…');
      expect(() => renderer?.root.findByProps({ testID: 'markdown' })).toThrow();
      expandActivityPanel(renderer!);
      expect(renderedText(renderer!)).toContain('# Still streaming');
      expect(() => renderer?.root.findByProps({ testID: 'markdown' })).toThrow();

      setUseChatResult({
        items: [{ id: 'a-done', kind: 'assistant', text: '# Finished heading', streaming: false }],
      });
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(renderer?.root.findByProps({ testID: 'markdown' })).toBeTruthy();
      expect(renderedText(renderer!)).toContain('Finished heading');
    });

    it('routes markdown file: links to the Files tab with a path param (M9-03)', () => {
      setUseChatResult({
        items: [
          {
            id: 'a-done',
            kind: 'assistant',
            text: 'Saved as [x.md](file:/workspace/notes/x.md)',
            streaming: false,
          },
        ],
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      const link = renderer?.root
        .findAllByProps({ testID: 'file-link' })
        .find((node) => typeof (node.props as { onPress?: unknown }).onPress === 'function');
      act(() => {
        (link?.props as { onPress: () => void }).onPress();
      });

      expect(mockPush).toHaveBeenCalledWith({
        pathname: '/files',
        params: { path: 'notes/x.md' },
      });
    });

    it('expanded panel shows reasoning as dimmed italic text (M8-07)', () => {
      setUseChatResult({
        items: [
          { id: 'u1', kind: 'user', text: '2+2?' },
          { id: 'r1', kind: 'reasoning', text: 'The user asked 2+2. That is 4.' },
          { id: 'a1', kind: 'assistant', text: '4', streaming: true },
        ],
        turns: groupItemsIntoTurns(
          [
            { id: 'u1', kind: 'user', text: '2+2?' },
            { id: 'r1', kind: 'reasoning', text: 'The user asked 2+2. That is 4.' },
            { id: 'a1', kind: 'assistant', text: '4', streaming: true },
          ],
          { u1: { status: 'running' } },
        ),
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(renderer?.root.findByProps({ testID: 'turn-activity-status' }).props.children).toBe(
        'Writing…',
      );
      expect(() => renderer?.root.findByProps({ testID: 'turn-activity-reasoning' })).toThrow();

      expandActivityPanel(renderer!);
      const reasoning = renderer!.root.findByProps({ testID: 'turn-activity-reasoning' });
      expect(reasoning.props.children).toBe('The user asked 2+2. That is 4.');
      expect(reasoning.props.style).toEqual(
        expect.objectContaining({ fontStyle: 'italic' }),
      );
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
      expandActivityPanel(renderer!);

      expect(renderer?.root.findByProps({ testID: 'chat-item-tool-rejected-chip' })).toBeTruthy();
      expect(renderedText(renderer!)).toContain('rejected');
    });
  });

  describe('Edit / Resend / Regenerate (M8-04)', () => {
    const userOne: ChatUserItem = { id: 'u-1', kind: 'user', text: 'turn one' };
    const assistantOne = { id: 'a-1', kind: 'assistant' as const, text: 'reply one', streaming: false };
    const userTwo: ChatUserItem = { id: 'u-2', kind: 'user', text: 'turn two' };
    const assistantTwo = { id: 'a-2', kind: 'assistant' as const, text: 'reply two', streaming: false };

    it('long-press on a user bubble opens Edit + Resend; Edit prefills the composer and shows the banner', () => {
      setUseChatResult({ items: [userOne, assistantOne, userTwo, assistantTwo] });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(() => renderer?.root.findByProps({ testID: 'chat-edit-banner' })).toThrow();

      const userBubbles = renderer!.root
        .findAllByProps({ testID: 'chat-item-user-bubble' })
        .filter((node) => typeof (node.props as { onLongPress?: unknown }).onLongPress === 'function');
      act(() => {
        (userBubbles[1].props as { onLongPress: () => void }).onLongPress();
      });

      expect(renderer?.root.findByProps({ testID: 'chat-message-menu' })).toBeTruthy();
      expect(renderer?.root.findByProps({ testID: 'chat-message-action-copy' })).toBeTruthy();
      expect(renderer?.root.findByProps({ testID: 'chat-message-action-edit' })).toBeTruthy();
      expect(renderer?.root.findByProps({ testID: 'chat-message-action-resend' })).toBeTruthy();

      act(() => {
        (renderer!.root.findByProps({ testID: 'chat-message-action-edit' }).props as { onPress: () => void }).onPress();
      });

      expect(renderer?.root.findByProps({ testID: 'chat-edit-banner' })).toBeTruthy();
      expect(renderedText(renderer!)).toContain('Editing — sending replaces everything after this message');
      const input = renderer?.root.findByProps({ placeholder: 'Message…' });
      expect((input?.props as { value: string }).value).toBe('turn two');
    });

    it('Cancel on the edit banner dismisses it and clears the composer', () => {
      setUseChatResult({ items: [userOne, assistantOne] });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      const cancelUserBubble = renderer!.root
        .findAllByProps({ testID: 'chat-item-user-bubble' })
        .find((node) => typeof (node.props as { onLongPress?: unknown }).onLongPress === 'function');
      act(() => {
        (cancelUserBubble?.props as { onLongPress: () => void }).onLongPress();
      });
      act(() => {
        (renderer!.root.findByProps({ testID: 'chat-message-action-edit' }).props as { onPress: () => void }).onPress();
      });
      expect(renderer?.root.findByProps({ testID: 'chat-edit-banner' })).toBeTruthy();

      act(() => {
        (renderer!.root.findByProps({ testID: 'chat-edit-cancel' }).props as { onPress: () => void }).onPress();
      });

      expect(() => renderer?.root.findByProps({ testID: 'chat-edit-banner' })).toThrow();
      const input = renderer?.root.findByProps({ placeholder: 'Message…' });
      expect((input?.props as { value: string }).value).toBe('');
    });

    it('Resend sends the user text with replaceFromMessageId and mode truncate', () => {
      const sendMessage = jest.fn();
      setUseChatResult({ items: [userOne, assistantOne], sendMessage });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      const resendUserBubble = renderer!.root
        .findAllByProps({ testID: 'chat-item-user-bubble' })
        .find((node) => typeof (node.props as { onLongPress?: unknown }).onLongPress === 'function');
      act(() => {
        (resendUserBubble?.props as { onLongPress: () => void }).onLongPress();
      });
      act(() => {
        (renderer!.root.findByProps({ testID: 'chat-message-action-resend' }).props as { onPress: () => void }).onPress();
      });

      expect(sendMessage).toHaveBeenCalledWith('turn one', { replaceFromMessageId: 'u-1', mode: 'truncate' });
    });

    it('Regenerate on the last assistant resends the preceding user message unchanged', () => {
      const sendMessage = jest.fn();
      setUseChatResult({ items: [userOne, assistantOne, userTwo, assistantTwo], sendMessage });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      const assistantBubbles = renderer!.root
        .findAllByProps({ testID: 'chat-item-assistant-bubble' })
        .filter((node) => typeof (node.props as { onLongPress?: unknown }).onLongPress === 'function');
      const lastBubble = assistantBubbles[assistantBubbles.length - 1];
      expect((lastBubble.props as { onLongPress?: () => void }).onLongPress).toBeDefined();

      act(() => {
        (lastBubble.props as { onLongPress: () => void }).onLongPress();
      });
      act(() => {
        (renderer!.root.findByProps({ testID: 'chat-message-action-regenerate' }).props as { onPress: () => void }).onPress();
      });

      expect(sendMessage).toHaveBeenCalledWith('turn two', { replaceFromMessageId: 'u-2', mode: 'truncate' });
    });

    it('Copy on the assistant menu copies the message text', () => {
      setUseChatResult({ items: [userOne, assistantOne] });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      const assistantBubbles = renderer!.root
        .findAllByProps({ testID: 'chat-item-assistant-bubble' })
        .filter((node) => typeof (node.props as { onLongPress?: unknown }).onLongPress === 'function');
      act(() => {
        (assistantBubbles[0].props as { onLongPress: () => void }).onLongPress();
      });
      act(() => {
        (renderer!.root.findByProps({ testID: 'chat-message-action-copy' }).props as { onPress: () => void }).onPress();
      });

      expect(mockCopyToClipboard).toHaveBeenCalledWith('reply one');
    });
  });

  describe('Branch switcher + edit mode (M8-05)', () => {
    const userOne: ChatUserItem = { id: 'u-1', kind: 'user', text: 'turn one' };
    const assistantOne = { id: 'a-1', kind: 'assistant' as const, text: 'reply one', streaming: false };
    const userTwo: ChatUserItem = { id: 'u-2', kind: 'user', text: 'turn two forked' };
    const assistantTwo = { id: 'a-2', kind: 'assistant' as const, text: 'forked reply', streaming: false };

    const branchPoint = {
      anchor_message_id: 'u-2',
      active_index: 1,
      branches: [
        { checkpoint_id: 'tip-a', preview: 'turn two', created_at: '2026-01-01T00:00:00Z' },
        { checkpoint_id: 'tip-b', preview: 'turn two forked', created_at: '2026-01-01T00:01:00Z' },
      ],
    };

    it('shows ‹ 2/2 › on a branch-anchor user bubble and prev calls switchBranch', () => {
      const switchBranch = jest.fn();
      setUseChatResult({
        items: [userOne, assistantOne, userTwo, assistantTwo],
        branches: [branchPoint],
        switchBranch,
      });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(renderer?.root.findByProps({ testID: 'chat-branch-switcher' })).toBeTruthy();
      expect(renderer?.root.findByProps({ testID: 'chat-branch-label' }).props.children).toBe('2/2');

      act(() => {
        (renderer!.root.findByProps({ testID: 'chat-branch-prev' }).props as { onPress: () => void }).onPress();
      });
      expect(switchBranch).toHaveBeenCalledWith('tip-a');
    });

    it('does not show a switcher when there are no branch points', () => {
      setUseChatResult({ items: [userOne, assistantOne] });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(() => renderer?.root.findByProps({ testID: 'chat-branch-switcher' })).toThrow();
    });

    it('edit banner offers Replace / Branch, preselected from edit_mode_default, and send uses the choice', () => {
      mockEditModeDefault = 'fork';
      const sendMessage = jest.fn();
      setUseChatResult({ items: [userOne, assistantOne], sendMessage });

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      const userBubble = renderer!.root
        .findAllByProps({ testID: 'chat-item-user-bubble' })
        .find((node) => typeof (node.props as { onLongPress?: unknown }).onLongPress === 'function');
      act(() => {
        (userBubble?.props as { onLongPress: () => void }).onLongPress();
      });
      act(() => {
        (renderer!.root.findByProps({ testID: 'chat-message-action-edit' }).props as { onPress: () => void }).onPress();
      });

      expect(renderedText(renderer!)).toContain('keeps the old continuation as a branch');
      expect(renderer?.root.findByProps({ testID: 'chat-edit-mode-fork' })).toBeTruthy();
      expect(renderer?.root.findByProps({ testID: 'chat-edit-mode-truncate' })).toBeTruthy();

      const input = renderer?.root.findByProps({ placeholder: 'Message…' });
      act(() => {
        (input?.props as { onChangeText: (value: string) => void }).onChangeText('turn one forked');
      });
      act(() => {
        (renderer!.root.findByProps({ accessibilityLabel: 'Send message' }).props as { onPress: () => void }).onPress();
      });

      expect(sendMessage).toHaveBeenCalledWith('turn one forked', {
        replaceFromMessageId: 'u-1',
        mode: 'fork',
      });
    });
  });

  describe('Voice input (M9-06)', () => {
    it('hides the mic button when speech is unsupported', () => {
      mockIsSpeechSupported.mockReturnValue(false);
      setUseChatResult();

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(() => renderer?.root.findByProps({ testID: 'chat-mic' })).toThrow();
    });

    it('hides the mic button in an insecure context (speech reports unsupported)', () => {
      mockIsSpeechSupported.mockReturnValue(false);
      setUseChatResult();

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      expect(() => renderer?.root.findByProps({ testID: 'chat-mic' })).toThrow();
    });

    it('shows the mic button when speech is supported and appends interim then final', () => {
      mockIsSpeechSupported.mockReturnValue(true);
      let captured: {
        onInterim?: (text: string) => void;
        onFinal?: (text: string) => void;
        onError?: (error: string) => void;
      } = {};
      mockStartListening.mockImplementation((options: StartListeningOptions) => {
        captured = options;
        return () => {};
      });
      setUseChatResult();

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      const mic = renderer!.root.findByProps({ testID: 'chat-mic' });
      expect(mic.props.accessibilityLabel).toBe('Start voice input');

      act(() => {
        (mic.props as { onPress: () => void }).onPress();
      });
      expect(mockStartListening).toHaveBeenCalled();

      act(() => {
        captured.onInterim?.('hello');
      });
      expect(renderer!.root.findByProps({ testID: 'chat-speech-interim' }).props.children).toBe('hello');

      act(() => {
        captured.onFinal?.('hello world');
      });
      expect(renderer!.root.findAllByProps({ testID: 'chat-speech-interim' })).toHaveLength(0);
      const input = renderer!.root.findByProps({ placeholder: 'Message…' });
      expect(input.props.value).toBe('hello world');
    });

    it('toasts a microphone hint on not-allowed / audio-capture errors', () => {
      mockIsSpeechSupported.mockReturnValue(true);
      let captured: { onError?: (error: string) => void } = {};
      mockStartListening.mockImplementation((options: StartListeningOptions) => {
        captured = options;
        return () => {};
      });
      setUseChatResult();

      let renderer: ReturnType<typeof create> | undefined;
      act(() => {
        renderer = create(createElement(ChatScreen));
      });

      act(() => {
        (renderer!.root.findByProps({ testID: 'chat-mic' }).props as { onPress: () => void }).onPress();
      });
      act(() => {
        captured.onError?.('not-allowed');
      });

      expect(JSON.stringify(renderer!.toJSON())).toContain('Allow microphone for homeai.local');
    });
  });
});
