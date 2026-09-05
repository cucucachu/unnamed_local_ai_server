import { createElement } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import type { ThreadMessage } from '../threads';
import type { WebSocketCtor, WebSocketLike } from '../chatSocket';
import { mapHistoryToItems, useChat, type ChatItem, type UseChatResult } from '../useChat';

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

/** Mocks `global.fetch` (the layer `lib/threads.ts`'s `getThreadMessages` ->
 * `lib/api.ts`'s `apiFetch` ultimately calls) to resolve `GET
 * /api/threads/{id}/messages` with `messages` — same mocking approach as
 * `lib/__tests__/api.test.ts`, reused here for consistency. */
function mockThreadMessages(messages: ThreadMessage[]): void {
  global.fetch = jest.fn().mockImplementation((url: string) => {
    const href = String(url);
    if (href.includes('/branches')) {
      return Promise.resolve({ ok: true, status: 200, statusText: 'OK', json: async () => [] });
    }
    if (href.includes('/state')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        statusText: 'OK',
        json: async () => ({ pending_approval: null }),
      });
    }
    if (href.includes('/active_branch')) {
      return Promise.resolve({ ok: true, status: 204, statusText: 'No Content', json: async () => undefined });
    }
    return Promise.resolve({ ok: true, status: 200, statusText: 'OK', json: async () => messages });
  }) as unknown as typeof fetch;
}

function mockThreadMessagesFailure(status = 500, detail = 'boom'): void {
  global.fetch = jest.fn().mockResolvedValue({
    ok: false,
    status,
    statusText: 'Internal Server Error',
    json: async () => ({ detail }),
  }) as unknown as typeof fetch;
}

/** Minimal `renderHook`-equivalent: no `@testing-library/react-hooks` is
 * installed, so this mounts a throwaway component via `react-test-renderer`
 * (already a transitive dep of `jest-expo`) and captures the hook's return
 * value on every render. This variant does NOT wait for the hydration
 * fetch's pending promise chain to settle — used by the hydration-phase
 * tests themselves, which need to assert on the `'loading'` state before it
 * resolves. */
function renderUseChatSync(threadId = 'default'): {
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

/** Flushes the microtask queue via a zero-delay macrotask boundary, letting
 * the hydration fetch's `.then()`/`.catch()` chain (and the state updates
 * inside it) settle before the caller continues. */
async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

/** `renderUseChatSync` + an immediate `flush()` — the harness every
 * non-hydration-focused test in this file uses, since those tests only
 * care about post-hydration (`'done'`) socket behavior and would otherwise
 * all need their own explicit `flush()` call. */
async function renderUseChat(threadId = 'default'): Promise<ReturnType<typeof renderUseChatSync>> {
  const hook = renderUseChatSync(threadId);
  await flush();
  return hook;
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
  // Default: empty history, hydration succeeds immediately — matches this
  // suite's pre-M3-04 behavior of starting every test with an empty
  // `items` list and an immediately-open socket. Tests that care about
  // hydration itself (loading/error/retry) override this per-test.
  mockThreadMessages([]);
});

describe('useChat — plain text turn', () => {
  it('produces one assistant item with the full concatenated text, streaming: false after turn_end', async () => {
    const hook = await renderUseChat();

    act(() => {
      latestSocket().emit({ type: 'turn_start' });
      latestSocket().emit({ type: 'token', content: 'hello ' });
      latestSocket().emit({ type: 'token', content: 'world' });
      latestSocket().emit({ type: 'turn_end', status: 'completed' });
    });

    const { items, busy } = hook.current();
    expect(items).toHaveLength(1);
    const assistant = findItem(items, 'assistant');
    expect(assistant.text).toBe('hello world');
    expect(assistant.streaming).toBe(false);
    expect(busy).toBe(false);

    const { turns } = hook.current();
    expect(turns).toHaveLength(1);
    expect(turns[0].final?.text).toBe('hello world');
    expect(turns[0].activity).toEqual([]);
    expect(turns[0].status).toBe('completed');
  });
});

describe('useChat — reasoning frames (M8-07)', () => {
  it('accumulates reasoning into the current turn activity and keeps it out of final', async () => {
    const hook = await renderUseChat();

    act(() => {
      hook.current().sendMessage('what is 2+2?');
    });
    act(() => {
      latestSocket().emit({ type: 'turn_start' });
      latestSocket().emit({ type: 'reasoning', content: 'The user asked ' });
      latestSocket().emit({ type: 'reasoning', content: '2+2.' });
    });

    const mid = hook.current();
    const reasoning = findItem(mid.items, 'reasoning');
    expect(reasoning.text).toBe('The user asked 2+2.');
    expect(mid.turns[0].activity.some((item) => item.kind === 'reasoning')).toBe(true);
    expect(mid.turns[0].status).toBe('running');

    act(() => {
      latestSocket().emit({ type: 'token', content: '4' });
      latestSocket().emit({ type: 'turn_end', status: 'completed', duration_ms: 1200 });
    });

    const { items, turns } = hook.current();
    expect(findItem(items, 'reasoning').text).toBe('The user asked 2+2.');
    expect(turns[0].activity.map((i) => i.kind)).toEqual(['reasoning']);
    expect(turns[0].final?.text).toBe('4');
    expect(turns[0].status).toBe('completed');
  });

  it('does not invent reasoning items from history hydration', async () => {
    mockThreadMessages([
      { id: 'u1', role: 'user', content: 'hi', tool_name: null, tool_calls: null },
      { id: 'a1', role: 'assistant', content: 'hello', tool_name: null, tool_calls: null },
    ]);
    const hook = await renderUseChat();
    expect(hook.current().items.some((item) => item.kind === 'reasoning')).toBe(false);
    expect(hook.current().turns[0].activity.some((item) => item.kind === 'reasoning')).toBe(false);
  });
});

describe('useChat — tool turn', () => {
  it('produces a tool item with the right status/name/category, and a SEPARATE assistant item after it', async () => {
    const hook = await renderUseChat();

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
      latestSocket().emit({ type: 'turn_end', status: 'completed' });
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

    const turn = hook.current().turns[0];
    expect(turn.activity.map((i) => i.kind)).toEqual(['tool']);
    expect(turn.final?.text).toBe('done');
    expect(turn.status).toBe('completed');
  });
});

describe('useChat — error frame', () => {
  it('appends an error item and resets busy to false', async () => {
    const hook = await renderUseChat();

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

  it('closes out a dangling streaming assistant item instead of leaving it stuck streaming', async () => {
    const hook = await renderUseChat();

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
  it('grows items to include two independent sets of turn output', async () => {
    const hook = await renderUseChat();

    act(() => {
      hook.current().sendMessage('message one');
      latestSocket().emit({ type: 'turn_start' });
      latestSocket().emit({ type: 'token', content: 'first reply' });
      latestSocket().emit({ type: 'turn_end', status: 'completed' });
    });

    expect(hook.current().items).toHaveLength(2); // user + assistant
    expect(hook.current().busy).toBe(false);

    act(() => {
      hook.current().sendMessage('message two');
      latestSocket().emit({ type: 'turn_start' });
      latestSocket().emit({ type: 'token', content: 'second reply' });
      latestSocket().emit({ type: 'turn_end', status: 'completed' });
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
  it('ends with zero dangling streaming items — only the tool item remains', async () => {
    const hook = await renderUseChat();

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
      latestSocket().emit({ type: 'turn_end', status: 'completed' });
    });

    const { items, busy } = hook.current();
    expect(items).toHaveLength(1);
    expect(items[0].kind).toBe('tool');
    expect(busy).toBe(false);
  });
});

describe('useChat — stopTurn / cancelled turn_end (M8-01)', () => {
  it('marks the currently-streaming assistant item stopped:true on turn_end status "cancelled", and clears busy', async () => {
    const hook = await renderUseChat();

    act(() => {
      hook.current().sendMessage('count slowly');
      latestSocket().emit({ type: 'turn_start' });
      latestSocket().emit({ type: 'token', content: 'one two three' });
    });
    expect(hook.current().busy).toBe(true);

    act(() => {
      hook.current().stopTurn();
    });
    expect(latestSocket().sent).toContain(JSON.stringify({ type: 'cancel' }));

    act(() => {
      latestSocket().emit({ type: 'turn_end', status: 'cancelled' });
    });

    const { items, busy } = hook.current();
    expect(busy).toBe(false);
    const assistant = findItem(items, 'assistant');
    expect(assistant.text).toBe('one two three');
    expect(assistant.streaming).toBe(false);
    expect(assistant.stopped).toBe(true);

    const cancelledTurn = hook.current().turns[0];
    expect(cancelledTurn.status).toBe('cancelled');
    expect(cancelledTurn.final).toBeNull();
    expect(cancelledTurn.activity.some((item) => item.kind === 'assistant' && item.stopped)).toBe(true);
  });

  it('does NOT mark the item stopped on a normal turn_end status "completed"', async () => {
    const hook = await renderUseChat();

    act(() => {
      latestSocket().emit({ type: 'turn_start' });
      latestSocket().emit({ type: 'token', content: 'hello' });
      latestSocket().emit({ type: 'turn_end', status: 'completed' });
    });

    const assistant = findItem(hook.current().items, 'assistant');
    expect(assistant.stopped).toBeFalsy();
  });

  it('a cancel with no streaming item in flight is a harmless no-op', async () => {
    const hook = await renderUseChat();

    act(() => {
      hook.current().stopTurn();
    });

    expect(latestSocket().sent).toEqual([JSON.stringify({ type: 'cancel' })]);
    expect(hook.current().items).toEqual([]);
    expect(hook.current().busy).toBe(false);
  });
});

describe('useChat — sendMessage', () => {
  it('appends a user item immediately, sets busy, and sends a user_message frame', async () => {
    const hook = await renderUseChat();

    act(() => {
      hook.current().sendMessage('hi there');
    });

    const user = findItem(hook.current().items, 'user');
    expect(user.text).toBe('hi there');
    expect(hook.current().busy).toBe(true);
    expect(JSON.parse(latestSocket().sent[0])).toEqual({
      type: 'user_message',
      content: 'hi there',
      id: user.id,
    });
  });

  it('replaceFromMessageId drops items from that user item onward and sends truncate fields (M8-04)', async () => {
    const hook = await renderUseChat();

    act(() => {
      hook.current().sendMessage('one');
    });
    const firstId = findItem(hook.current().items, 'user').id;

    act(() => {
      latestSocket().emit({ type: 'turn_start' });
      latestSocket().emit({ type: 'token', content: 'reply one' });
      latestSocket().emit({ type: 'turn_end', status: 'completed' });
      hook.current().sendMessage('two');
    });
    act(() => {
      latestSocket().emit({ type: 'turn_start' });
      latestSocket().emit({ type: 'token', content: 'reply two' });
      latestSocket().emit({ type: 'turn_end', status: 'completed' });
    });

    expect(hook.current().items.map((item) => item.kind)).toEqual(['user', 'assistant', 'user', 'assistant']);

    act(() => {
      hook.current().sendMessage('one edited', { replaceFromMessageId: firstId, mode: 'truncate' });
    });

    const items = hook.current().items;
    expect(items).toHaveLength(1);
    expect(items[0]).toEqual(expect.objectContaining({ kind: 'user', text: 'one edited' }));
    expect(hook.current().busy).toBe(true);

    const sent = JSON.parse(latestSocket().sent[latestSocket().sent.length - 1]);
    expect(sent).toEqual({
      type: 'user_message',
      content: 'one edited',
      replace_from_message_id: firstId,
      mode: 'truncate',
      id: items[0].id,
    });
  });
});

describe('useChat — branches (M8-05)', () => {
  const branchPoint = {
    anchor_message_id: 'u-2',
    active_index: 1,
    branches: [
      { checkpoint_id: 'tip-a', preview: 'turn two', created_at: '2026-01-01T00:00:00Z' },
      { checkpoint_id: 'tip-b', preview: 'turn two forked', created_at: '2026-01-01T00:01:00Z' },
    ],
  };

  it('hydrates branches from GET /api/threads/{id}/branches', async () => {
    global.fetch = jest.fn().mockImplementation((url: string) => {
      const href = String(url);
      if (href.includes('/branches')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          statusText: 'OK',
          json: async () => [branchPoint],
        });
      }
      if (href.includes('/state')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          statusText: 'OK',
          json: async () => ({ pending_approval: null }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, statusText: 'OK', json: async () => [] });
    }) as unknown as typeof fetch;

    const hook = await renderUseChat();
    expect(hook.current().branches).toEqual([branchPoint]);
  });

  it('switchBranch PUTs the tip then re-hydrates', async () => {
    const putUrls: string[] = [];
    global.fetch = jest.fn().mockImplementation((url: string, init?: RequestInit) => {
      const href = String(url);
      if (href.includes('/active_branch')) {
        putUrls.push(href);
        expect(init?.method).toBe('PUT');
        expect(init?.body).toBe(JSON.stringify({ checkpoint_id: 'tip-a' }));
        return Promise.resolve({
          ok: true,
          status: 204,
          statusText: 'No Content',
          json: async () => undefined,
        });
      }
      if (href.includes('/branches')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          statusText: 'OK',
          json: async () => [branchPoint],
        });
      }
      if (href.includes('/state')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          statusText: 'OK',
          json: async () => ({ pending_approval: null }),
        });
      }
      if (href.includes('/messages')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          statusText: 'OK',
          json: async () => [
            { id: 'u-1', role: 'user', content: 'turn one', tool_name: null, tool_calls: null },
            { id: 'u-2', role: 'user', content: 'turn two', tool_name: null, tool_calls: null },
          ],
        });
      }
      return Promise.resolve({ ok: true, status: 200, statusText: 'OK', json: async () => [] });
    }) as unknown as typeof fetch;

    const hook = await renderUseChat();
    await act(async () => {
      await hook.current().switchBranch('tip-a');
    });
    await flush();

    expect(putUrls).toHaveLength(1);
    expect(hook.current().items.map((item) => item.kind)).toEqual(['user', 'user']);
    expect(hook.current().hydrationState).toBe('done');
  });
});

describe('useChat — HITL approvals (M8-03)', () => {
  const action = {
    tool_call_id: 'call-1',
    name: 'write_file',
    category: 'file' as const,
    args: { file_path: '/x.txt', content: 'y' },
    description: 'Write file `/x.txt`',
  };

  it('approval_request populates pendingApproval and turn_end "awaiting_approval" does not clear it', async () => {
    const hook = await renderUseChat();

    act(() => {
      latestSocket().emit({ type: 'turn_start' });
      latestSocket().emit({
        type: 'tool_start',
        tool_call_id: 'ignored',
        name: 'write_file',
        category: 'file',
        args: {},
      });
      latestSocket().emit({ type: 'approval_request', interrupt_id: 'int-1', actions: [action] });
      latestSocket().emit({ type: 'turn_end', status: 'awaiting_approval' });
    });

    const { pendingApproval, busy } = hook.current();
    expect(busy).toBe(false);
    expect(pendingApproval).toEqual({
      interruptId: 'int-1',
      actions: [
        {
          toolCallId: 'call-1',
          name: 'write_file',
          category: 'file',
          args: { file_path: '/x.txt', content: 'y' },
          description: 'Write file `/x.txt`',
        },
      ],
    });
  });

  it('respondToApproval sends approval_response, clears pendingApproval, and sets busy', async () => {
    const hook = await renderUseChat();

    act(() => {
      latestSocket().emit({ type: 'approval_request', interrupt_id: 'int-1', actions: [action] });
      latestSocket().emit({ type: 'turn_end', status: 'awaiting_approval' });
    });
    expect(hook.current().pendingApproval).not.toBeNull();

    act(() => {
      hook.current().respondToApproval([{ tool_call_id: 'call-1', decision: 'approve' }]);
    });

    expect(hook.current().pendingApproval).toBeNull();
    expect(hook.current().busy).toBe(true);
    expect(latestSocket().sent).toContain(
      JSON.stringify({
        type: 'approval_response',
        interrupt_id: 'int-1',
        decisions: [{ tool_call_id: 'call-1', decision: 'approve' }],
      }),
    );
  });

  it('respondToApproval with a reject decision synthesizes a rejected ChatToolItem immediately', async () => {
    const hook = await renderUseChat();

    act(() => {
      latestSocket().emit({ type: 'approval_request', interrupt_id: 'int-1', actions: [action] });
      latestSocket().emit({ type: 'turn_end', status: 'awaiting_approval' });
    });

    act(() => {
      hook.current().respondToApproval([{ tool_call_id: 'call-1', decision: 'reject' }]);
    });

    const tool = findItem(hook.current().items, 'tool');
    expect(tool).toMatchObject({
      toolCallId: 'call-1',
      name: 'write_file',
      category: 'file',
      status: 'rejected',
      args: { file_path: '/x.txt', content: 'y' },
    });
  });

  it('respondToApproval is a no-op if called again after the first response (avoids double-submit)', async () => {
    const hook = await renderUseChat();

    act(() => {
      latestSocket().emit({ type: 'approval_request', interrupt_id: 'int-1', actions: [action] });
      latestSocket().emit({ type: 'turn_end', status: 'awaiting_approval' });
    });

    act(() => {
      hook.current().respondToApproval([{ tool_call_id: 'call-1', decision: 'approve' }]);
      hook.current().respondToApproval([{ tool_call_id: 'call-1', decision: 'reject' }]);
    });

    const sentApprovalResponses = latestSocket().sent.filter((s) => JSON.parse(s).type === 'approval_response');
    expect(sentApprovalResponses).toHaveLength(1);
  });

  it('a real (approved) tool_start/tool_end after the resumed turn produces a normal (non-rejected) tool item', async () => {
    const hook = await renderUseChat();

    act(() => {
      latestSocket().emit({ type: 'approval_request', interrupt_id: 'int-1', actions: [action] });
      latestSocket().emit({ type: 'turn_end', status: 'awaiting_approval' });
      hook.current().respondToApproval([{ tool_call_id: 'call-1', decision: 'approve' }]);
    });

    act(() => {
      latestSocket().emit({ type: 'turn_start' });
      latestSocket().emit({
        type: 'tool_start',
        tool_call_id: 'call-1',
        name: 'write_file',
        category: 'file',
        args: { file_path: '/x.txt', content: 'y' },
      });
      latestSocket().emit({
        type: 'tool_end',
        tool_call_id: 'call-1',
        name: 'write_file',
        status: 'success',
        result_preview: 'wrote 1 file',
      });
      latestSocket().emit({ type: 'turn_end', status: 'completed' });
    });

    const tool = findItem(hook.current().items, 'tool');
    expect(tool.status).toBe('success');
  });
});

describe('useChat — pending approval hydration (M8-03)', () => {
  it('restores pendingApproval from GET /api/threads/{id}/state after history hydration', async () => {
    global.fetch = jest.fn().mockImplementation((url: string) => {
      if (url.includes('/state')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          statusText: 'OK',
          json: async () => ({
            pending_approval: {
              interrupt_id: 'int-restored',
              actions: [
                {
                  tool_call_id: 'call-9',
                  name: 'delete',
                  category: 'file',
                  args: { file_path: '/y.txt' },
                  description: 'Delete `/y.txt`',
                },
              ],
            },
          }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, statusText: 'OK', json: async () => [] });
    }) as unknown as typeof fetch;

    const hook = renderUseChatSync();
    await flush();

    expect(hook.current().hydrationState).toBe('done');
    expect(hook.current().pendingApproval).toEqual({
      interruptId: 'int-restored',
      actions: [
        {
          toolCallId: 'call-9',
          name: 'delete',
          category: 'file',
          args: { file_path: '/y.txt' },
          description: 'Delete `/y.txt`',
        },
      ],
    });
  });

  it('leaves pendingApproval null when GET .../state reports none pending', async () => {
    global.fetch = jest.fn().mockImplementation((url: string) => {
      if (url.includes('/state')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          statusText: 'OK',
          json: async () => ({ pending_approval: null }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, statusText: 'OK', json: async () => [] });
    }) as unknown as typeof fetch;

    const hook = renderUseChatSync();
    await flush();

    expect(hook.current().pendingApproval).toBeNull();
  });
});

describe('useChat — connectionState', () => {
  it('reflects the underlying socket connection lifecycle', async () => {
    const hook = await renderUseChat();

    expect(hook.current().connectionState).toBe('connecting');

    act(() => {
      latestSocket().onopen?.({});
    });
    expect(hook.current().connectionState).toBe('open');
  });
});

describe('useChat — history hydration', () => {
  it('starts in hydrationState "loading" with the socket not yet opened', () => {
    mockThreadMessages([
      { id: 'm-1', role: 'user', content: 'hello from history', tool_name: null, tool_calls: null },
    ]);

    const hook = renderUseChatSync();

    expect(hook.current().hydrationState).toBe('loading');
    expect(hook.current().items).toEqual([]);
    expect(FakeWebSocket.instances).toHaveLength(0);

    // Unmount before the mocked fetch's promise settles (rather than
    // leaving it dangling into the next test): the hydration effect's own
    // cleanup sets a `cancelled` flag precisely so an in-flight hydration
    // fetch never calls `setState` after unmount.
    hook.unmount();
  });

  it('maps fetched history into items and opens the socket once hydration completes', async () => {
    mockThreadMessages([
      { id: 'm-1', role: 'user', content: 'hello from history', tool_name: null, tool_calls: null },
    ]);

    const hook = renderUseChatSync();
    await flush();

    expect(hook.current().hydrationState).toBe('done');
    expect(hook.current().items).toEqual([{ id: 'm-1', kind: 'user', text: 'hello from history' }]);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it('sets hydrationState "error" and never opens the socket when the history fetch fails', async () => {
    mockThreadMessagesFailure(500, 'db unavailable');

    const hook = renderUseChatSync();
    await flush();

    expect(hook.current().hydrationState).toBe('error');
    expect(hook.current().items).toEqual([]);
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it('retryHydration re-fetches and recovers into "done", opening the socket', async () => {
    mockThreadMessagesFailure();

    const hook = renderUseChatSync();
    await flush();
    expect(hook.current().hydrationState).toBe('error');

    mockThreadMessages([{ id: 'm-2', role: 'user', content: 'second attempt', tool_name: null, tool_calls: null }]);
    act(() => {
      hook.current().retryHydration();
    });
    expect(hook.current().hydrationState).toBe('loading');
    expect(FakeWebSocket.instances).toHaveLength(0);

    await flush();

    expect(hook.current().hydrationState).toBe('done');
    expect(hook.current().items).toEqual([{ id: 'm-2', kind: 'user', text: 'second attempt' }]);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});

describe('mapHistoryToItems', () => {
  // Table-driven per the ticket ("history→items mapping table-driven (≥4
  // cases incl. tool rows)"). Each case is exercised in isolation (its own
  // one- or two-message `input`) except where a case's whole point is an
  // interaction between adjacent rows (the "assistant+tool_calls paired
  // with the following tool row" case), matching how `_normalize_message`
  // in `app/api/chat.py` actually emits these sequences.
  const cases: { name: string; input: ThreadMessage[]; expected: ChatItem[] }[] = [
    {
      name: 'a user row maps directly to a ChatUserItem',
      input: [{ id: 'u-1', role: 'user', content: 'hi there', tool_name: null, tool_calls: null }],
      expected: [{ id: 'u-1', kind: 'user', text: 'hi there' }],
    },
    {
      name: 'an assistant row with plain content maps directly to a non-streaming ChatAssistantItem',
      input: [{ id: 'a-1', role: 'assistant', content: 'hello!', tool_name: null, tool_calls: null }],
      expected: [{ id: 'a-1', kind: 'assistant', text: 'hello!', streaming: false }],
    },
    {
      name: 'a tool row maps to a ChatToolItem with status "success" and the stored content as resultPreview',
      input: [{ id: 't-1', role: 'tool', content: 'wrote 1 file', tool_name: 'write_file', tool_calls: null }],
      expected: [
        {
          id: 't-1',
          kind: 'tool',
          toolCallId: 't-1',
          name: 'write_file',
          category: 'file',
          status: 'success',
          args: {},
          resultPreview: 'wrote 1 file',
        },
      ],
    },
    {
      name: 'an unrecognized tool name still maps to a ChatToolItem, falling back to category "other"',
      input: [{ id: 't-2', role: 'tool', content: 'did a thing', tool_name: 'some_future_tool', tool_calls: null }],
      expected: [
        {
          id: 't-2',
          kind: 'tool',
          toolCallId: 't-2',
          name: 'some_future_tool',
          category: 'other',
          status: 'success',
          args: {},
          resultPreview: 'did a thing',
        },
      ],
    },
    {
      name: 'an assistant row with empty content + tool_calls renders nothing (the paired tool row covers it)',
      input: [
        {
          id: 'a-2',
          role: 'assistant',
          content: '',
          tool_name: null,
          tool_calls: [{ id: 'call-1', name: 'write_file', args: { file_path: '/x.txt' } }],
        },
        { id: 't-3', role: 'tool', content: 'wrote 1 file', tool_name: 'write_file', tool_calls: null },
        { id: 'a-3', role: 'assistant', content: 'done', tool_name: null, tool_calls: null },
      ],
      expected: [
        {
          id: 't-3',
          kind: 'tool',
          toolCallId: 't-3',
          name: 'write_file',
          category: 'file',
          status: 'success',
          args: {},
          resultPreview: 'wrote 1 file',
        },
        { id: 'a-3', kind: 'assistant', text: 'done', streaming: false },
      ],
    },
    {
      name: 'a tool row recovers args from the paired assistant tool_calls via tool_call_id (M8-04)',
      input: [
        {
          id: 'a-5',
          role: 'assistant',
          content: '',
          tool_name: null,
          tool_calls: [{ id: 'call-9', name: 'write_file', args: { file_path: '/x.txt', content: 'y' } }],
        },
        {
          id: 't-5',
          role: 'tool',
          content: 'wrote 1 file',
          tool_name: 'write_file',
          tool_calls: null,
          tool_call_id: 'call-9',
        },
      ],
      expected: [
        {
          id: 't-5',
          kind: 'tool',
          toolCallId: 'call-9',
          name: 'write_file',
          category: 'file',
          status: 'success',
          args: { file_path: '/x.txt', content: 'y' },
          resultPreview: 'wrote 1 file',
        },
      ],
    },
    {
      name: 'an assistant row with empty content and NO tool_calls still maps to an (empty) ChatAssistantItem',
      input: [{ id: 'a-4', role: 'assistant', content: '', tool_name: null, tool_calls: null }],
      expected: [{ id: 'a-4', kind: 'assistant', text: '', streaming: false }],
    },
    {
      name: 'an empty history maps to an empty item list',
      input: [],
      expected: [],
    },
  ];

  for (const { name, input, expected } of cases) {
    it(name, () => {
      expect(mapHistoryToItems(input)).toEqual(expected);
    });
  }
});
