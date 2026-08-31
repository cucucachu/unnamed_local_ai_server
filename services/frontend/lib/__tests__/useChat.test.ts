import { createElement } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import type { WebSocketCtor, WebSocketLike } from '../chatSocket';
import { useChat, type ChatItem, type UseChatResult } from '../useChat';

/** Same fake-`WebSocket` pattern as `chatSocket.test.ts`'s `FakeWebSocket` —
 * duplicated (rather than imported) since that class isn't exported from
 * the other test file, but kept structurally identical. */
class FakeWebSocket implements WebSocketLike {
  static instances: FakeWebSocket[] = [];

  readonly sent: string[] = [];
  closed = false;
  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onclose: ((event: unknown) => void) | null = null;

  constructor(_url: string) {
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.closed = true;
    this.onclose?.({});
  }

  emit(frame: unknown): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

function latestSocket(): FakeWebSocket {
  const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  if (!socket) throw new Error('no FakeWebSocket instance was created');
  return socket;
}

const Ctor = FakeWebSocket as unknown as WebSocketCtor;

/** Minimal `renderHook`-equivalent: no `@testing-library/react-hooks` is
 * installed, so this mounts a throwaway component via `react-test-renderer`
 * (already a transitive dep of `jest-expo`) and captures the hook's return
 * value on every render. */
function renderUseChat(threadId = 'default'): {
  current: () => UseChatResult;
  unmount: () => void;
} {
  let latest: UseChatResult | undefined;

  function Harness(): null {
    latest = useChat(threadId, Ctor);
    return null;
  }

  let renderer: ReactTestRenderer;
  act(() => {
    renderer = create(createElement(Harness));
  });

  return {
    current: () => {
      if (!latest) throw new Error('useChat never rendered');
      return latest;
    },
    unmount: () => act(() => renderer.unmount()),
  };
}

function findItem<K extends ChatItem['kind']>(
  items: ChatItem[],
  kind: K,
  index = 0,
): Extract<ChatItem, { kind: K }> {
  const matches = items.filter((item) => item.kind === kind) as Extract<ChatItem, { kind: K }>[];
  const found = matches[index];
  if (!found) throw new Error(`no ${kind} item at index ${index} (items: ${JSON.stringify(items)})`);
  return found;
}

beforeEach(() => {
  FakeWebSocket.instances = [];
});

describe('useChat — plain text turn', () => {
  it('produces one assistant item with the full concatenated text, streaming: false after turn_end', () => {
    const hook = renderUseChat();

    act(() => {
      latestSocket().emit({ type: 'turn_start' });
      latestSocket().emit({ type: 'token', content: 'hello ' });
      latestSocket().emit({ type: 'token', content: 'world' });
      latestSocket().emit({ type: 'turn_end' });
    });

    const { items, busy } = hook.current();
    expect(items).toHaveLength(1);
    const assistant = findItem(items, 'assistant');
    expect(assistant.text).toBe('hello world');
    expect(assistant.streaming).toBe(false);
    expect(busy).toBe(false);
  });
});

describe('useChat — tool turn', () => {
  it('produces a tool item with the right status/name/category, and a SEPARATE assistant item after it', () => {
    const hook = renderUseChat();

    act(() => {
      latestSocket().emit({ type: 'turn_start' });
      latestSocket().emit({
        type: 'tool_start',
        tool_call_id: 'call-1',
        name: 'write_file',
        category: 'file',
        args: { file_path: '/x.txt' },
      });
    });

    // Mid-tool: assistant's pre-tool empty placeholder was dropped, tool is
    // running, no post-tool assistant item exists yet.
    expect(hook.current().items).toHaveLength(1);
    expect(findItem(hook.current().items, 'tool').status).toBe('running');

    act(() => {
      latestSocket().emit({
        type: 'tool_end',
        tool_call_id: 'call-1',
        name: 'write_file',
        status: 'success',
        result_preview: 'wrote 1 file',
      });
      latestSocket().emit({ type: 'token', content: 'done' });
      latestSocket().emit({ type: 'turn_end' });
    });

    const { items, busy } = hook.current();
    expect(items).toHaveLength(2);

    const tool = findItem(items, 'tool');
    expect(tool.name).toBe('write_file');
    expect(tool.category).toBe('file');
    expect(tool.status).toBe('success');
    expect(tool.resultPreview).toBe('wrote 1 file');
    expect(tool.toolCallId).toBe('call-1');

    const assistant = findItem(items, 'assistant');
    expect(assistant.text).toBe('done');
    expect(assistant.streaming).toBe(false);
    expect(busy).toBe(false);

    // Prove the split: the tool item comes before the post-tool assistant item.
    expect(items.indexOf(tool)).toBeLessThan(items.indexOf(assistant));
  });
});

describe('useChat — error frame', () => {
  it('appends an error item and resets busy to false', () => {
    const hook = renderUseChat();

    act(() => {
      hook.current().sendMessage('hi');
    });
    expect(hook.current().busy).toBe(true);

    act(() => {
      latestSocket().emit({ type: 'error', message: 'boom' });
    });

    const { items, busy } = hook.current();
    expect(busy).toBe(false);
    const errorItem = findItem(items, 'error');
    expect(errorItem.message).toBe('boom');
  });

  it('closes out a dangling streaming assistant item instead of leaving it stuck streaming', () => {
    const hook = renderUseChat();

    act(() => {
      latestSocket().emit({ type: 'turn_start' });
      latestSocket().emit({ type: 'token', content: 'partial' });
      latestSocket().emit({ type: 'error', message: 'chat socket disconnected while a turn was in progress' });
    });

    const { items } = hook.current();
    const assistant = findItem(items, 'assistant');
    expect(assistant.text).toBe('partial');
    expect(assistant.streaming).toBe(false);
    expect(findItem(items, 'error').message).toContain('disconnected');
  });
});

describe('useChat — two turns in sequence', () => {
  it('grows items to include two independent sets of turn output', () => {
    const hook = renderUseChat();

    act(() => {
      hook.current().sendMessage('message one');
      latestSocket().emit({ type: 'turn_start' });
      latestSocket().emit({ type: 'token', content: 'first reply' });
      latestSocket().emit({ type: 'turn_end' });
    });

    expect(hook.current().items).toHaveLength(2); // user + assistant
    expect(hook.current().busy).toBe(false);

    act(() => {
      hook.current().sendMessage('message two');
      latestSocket().emit({ type: 'turn_start' });
      latestSocket().emit({ type: 'token', content: 'second reply' });
      latestSocket().emit({ type: 'turn_end' });
    });

    const { items } = hook.current();
    expect(items).toHaveLength(4);
    expect(items[0]).toMatchObject({ kind: 'user', text: 'message one' });
    expect(items[1]).toMatchObject({ kind: 'assistant', text: 'first reply', streaming: false });
    expect(items[2]).toMatchObject({ kind: 'user', text: 'message two' });
    expect(items[3]).toMatchObject({ kind: 'assistant', text: 'second reply', streaming: false });

    // The two assistant items are genuinely distinct instances, not one
    // item being reused/mutated across turns.
    expect(items[1].id).not.toBe(items[3].id);
  });
});

describe('useChat — tool-only turn with no trailing text (substituted edge case)', () => {
  // The ticket's "concurrent/interleaved" scenario doesn't translate
  // meaningfully to a single frontend hook instance (there's only ever one
  // socket per `useChat` call — concurrency at the frontend layer would be
  // "two `useChat` instances", which is really just two independent copies
  // of the plain-text-turn test, not a new interaction to cover). Substituted
  // per the ticket's own suggestion: a turn that ends with a tool call and no
  // trailing text at all, exercising the "handle gracefully" dangling-item
  // note for `turn_end`.
  it('ends with zero dangling streaming items — only the tool item remains', () => {
    const hook = renderUseChat();

    act(() => {
      latestSocket().emit({ type: 'turn_start' });
      latestSocket().emit({
        type: 'tool_start',
        tool_call_id: 'call-2',
        name: 'ls',
        category: 'file',
        args: {},
      });
      latestSocket().emit({
        type: 'tool_end',
        tool_call_id: 'call-2',
        name: 'ls',
        status: 'success',
        result_preview: 'a.txt\nb.txt',
      });
      latestSocket().emit({ type: 'turn_end' });
    });

    const { items, busy } = hook.current();
    expect(items).toHaveLength(1);
    expect(items[0].kind).toBe('tool');
    expect(busy).toBe(false);
  });
});

describe('useChat — sendMessage', () => {
  it('appends a user item immediately, sets busy, and sends a user_message frame', () => {
    const hook = renderUseChat();

    act(() => {
      hook.current().sendMessage('hi there');
    });

    expect(hook.current().items).toEqual([expect.objectContaining({ kind: 'user', text: 'hi there' })]);
    expect(hook.current().busy).toBe(true);
    expect(latestSocket().sent).toEqual([JSON.stringify({ type: 'user_message', content: 'hi there' })]);
  });
});

describe('useChat — connectionState', () => {
  it('reflects the underlying socket connection lifecycle', () => {
    const hook = renderUseChat();

    expect(hook.current().connectionState).toBe('connecting');

    act(() => {
      latestSocket().onopen?.({});
    });
    expect(hook.current().connectionState).toBe('open');
  });
});
