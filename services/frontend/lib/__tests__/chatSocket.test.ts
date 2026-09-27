import { openChatSocket, WS_CLOSE_NOT_FOUND, WS_CLOSE_UNAUTHORIZED, type WebSocketCtor, type WebSocketLike } from '../chatSocket';
import { onUnauthorized, setSessionToken } from '../session';

class FakeWebSocket implements WebSocketLike {
  static instances: FakeWebSocket[] = [];

  readonly url: string;
  readonly options: { headers: Record<string, string> } | undefined;
  readonly sent: string[] = [];
  closed = false;
  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onclose: ((event: unknown) => void) | null = null;

  constructor(url: string, _protocols?: string | string[], options?: { headers: Record<string, string> }) {
    this.url = url;
    this.options = options;
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.closed = true;
    this.onclose?.({});
  }

  /** Test helper: simulate the server pushing a frame. */
  emit(frame: unknown): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }

  /** Test helper: simulate the handshake completing. */
  open(): void {
    this.onopen?.({});
  }

  /** Test helper: simulate an unsolicited drop (server/network went away). */
  drop(code?: number): void {
    this.closed = true;
    this.onclose?.({ code });
  }
}

function latestSocket(): FakeWebSocket {
  const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  if (!socket) throw new Error('no FakeWebSocket instance was created');
  return socket;
}

function makeHandlers() {
  return {
    onTurnStart: jest.fn(),
    onToken: jest.fn(),
    onReasoning: jest.fn(),
    onToolStart: jest.fn(),
    onToolEnd: jest.fn(),
    onTurnEnd: jest.fn(),
    onError: jest.fn(),
  };
}

const Ctor = FakeWebSocket as unknown as WebSocketCtor;

beforeEach(() => {
  FakeWebSocket.instances = [];
});

describe('openChatSocket — frame dispatch', () => {
  it('dispatches turn_start to onTurnStart', () => {
    const handlers = makeHandlers();
    openChatSocket('thread-1', handlers, Ctor);

    latestSocket().emit({ type: 'turn_start' });

    expect(handlers.onTurnStart).toHaveBeenCalledWith({ type: 'turn_start' });
  });

  it('dispatches token to onToken with content', () => {
    const handlers = makeHandlers();
    openChatSocket('thread-1', handlers, Ctor);

    latestSocket().emit({ type: 'token', content: 'hello' });

    expect(handlers.onToken).toHaveBeenCalledWith({ type: 'token', content: 'hello' });
  });

  it('dispatches reasoning to onReasoning with content (M8-07)', () => {
    const handlers = makeHandlers();
    openChatSocket('thread-1', handlers, Ctor);

    latestSocket().emit({ type: 'reasoning', content: 'let me think' });

    expect(handlers.onReasoning).toHaveBeenCalledWith({ type: 'reasoning', content: 'let me think' });
  });

  it('dispatches tool_start with tool_call_id/name/category/args', () => {
    const handlers = makeHandlers();
    openChatSocket('thread-1', handlers, Ctor);

    const frame = {
      type: 'tool_start',
      tool_call_id: 'abc-123',
      name: 'read_file',
      category: 'file',
      args: { path: '/tmp/x' },
    };
    latestSocket().emit(frame);

    expect(handlers.onToolStart).toHaveBeenCalledWith(frame);
  });

  it('dispatches tool_end with status/result_preview', () => {
    const handlers = makeHandlers();
    openChatSocket('thread-1', handlers, Ctor);

    const frame = {
      type: 'tool_end',
      tool_call_id: 'abc-123',
      name: 'read_file',
      status: 'success',
      result_preview: 'file contents...',
    };
    latestSocket().emit(frame);

    expect(handlers.onToolEnd).toHaveBeenCalledWith(frame);
  });

  it('dispatches turn_end to onTurnEnd, including its status field', () => {
    const handlers = makeHandlers();
    openChatSocket('thread-1', handlers, Ctor);

    latestSocket().emit({ type: 'turn_start' });
    latestSocket().emit({ type: 'turn_end', status: 'completed' });

    expect(handlers.onTurnEnd).toHaveBeenCalledWith({ type: 'turn_end', status: 'completed' });
  });

  it('dispatches a cancelled turn_end to onTurnEnd', () => {
    const handlers = makeHandlers();
    openChatSocket('thread-1', handlers, Ctor);

    latestSocket().emit({ type: 'turn_start' });
    latestSocket().emit({ type: 'turn_end', status: 'cancelled' });

    expect(handlers.onTurnEnd).toHaveBeenCalledWith({ type: 'turn_end', status: 'cancelled' });
  });

  it('dispatches error to onError with message', () => {
    const handlers = makeHandlers();
    openChatSocket('thread-1', handlers, Ctor);

    latestSocket().emit({ type: 'error', message: 'boom' });

    expect(handlers.onError).toHaveBeenCalledWith({ type: 'error', message: 'boom' });
  });

  it('tolerates an unknown frame type without throwing or calling any handler', () => {
    const handlers = makeHandlers();
    openChatSocket('thread-1', handlers, Ctor);

    expect(() => latestSocket().emit({ type: 'some_future_frame', whatever: true })).not.toThrow();

    expect(handlers.onTurnStart).not.toHaveBeenCalled();
    expect(handlers.onToken).not.toHaveBeenCalled();
    expect(handlers.onToolStart).not.toHaveBeenCalled();
    expect(handlers.onToolEnd).not.toHaveBeenCalled();
    expect(handlers.onTurnEnd).not.toHaveBeenCalled();
    expect(handlers.onError).not.toHaveBeenCalled();
  });

  it('ignores malformed (non-JSON) messages without throwing', () => {
    const handlers = makeHandlers();
    openChatSocket('thread-1', handlers, Ctor);

    expect(() => latestSocket().onmessage?.({ data: 'not json' })).not.toThrow();
    expect(handlers.onError).not.toHaveBeenCalled();
  });
});

describe('openChatSocket — send/close', () => {
  it('send() serializes a user_message frame', () => {
    const handlers = makeHandlers();
    const chat = openChatSocket('thread-1', handlers, Ctor);

    chat.send('hi there');

    expect(latestSocket().sent).toEqual([JSON.stringify({ type: 'user_message', content: 'hi there' })]);
  });

  it('send() includes replace_from_message_id, mode, and id when provided (M8-04)', () => {
    const handlers = makeHandlers();
    const chat = openChatSocket('thread-1', handlers, Ctor);

    chat.send('edited', { replaceFromMessageId: 'user-2', mode: 'truncate', id: 'new-id' });

    expect(latestSocket().sent).toEqual([
      JSON.stringify({
        type: 'user_message',
        content: 'edited',
        replace_from_message_id: 'user-2',
        mode: 'truncate',
        id: 'new-id',
      }),
    ]);
  });

  it('close() closes the underlying socket', () => {
    const handlers = makeHandlers();
    const chat = openChatSocket('thread-1', handlers, Ctor);

    chat.close();

    expect(latestSocket().closed).toBe(true);
  });

  it('cancel() serializes a cancel frame', () => {
    const handlers = makeHandlers();
    const chat = openChatSocket('thread-1', handlers, Ctor);

    chat.cancel();

    expect(latestSocket().sent).toEqual([JSON.stringify({ type: 'cancel' })]);
  });

  it('dispatches approval_request to onApprovalRequest (M8-03)', () => {
    const handlers = { ...makeHandlers(), onApprovalRequest: jest.fn() };
    openChatSocket('thread-1', handlers, Ctor);

    const frame = {
      type: 'approval_request',
      interrupt_id: 'int-1',
      actions: [
        {
          tool_call_id: 'call-1',
          name: 'write_file',
          category: 'file',
          args: { file_path: '/x.txt', content: 'y' },
          description: 'Write file `/x.txt`',
        },
      ],
    };
    latestSocket().emit(frame);

    expect(handlers.onApprovalRequest).toHaveBeenCalledWith(frame);
  });

  it('dispatches an awaiting_approval turn_end to onTurnEnd (M8-03)', () => {
    const handlers = makeHandlers();
    openChatSocket('thread-1', handlers, Ctor);

    latestSocket().emit({ type: 'turn_end', status: 'awaiting_approval' });

    expect(handlers.onTurnEnd).toHaveBeenCalledWith({ type: 'turn_end', status: 'awaiting_approval' });
  });

  it('approvalResponse() serializes an approval_response frame (M8-03)', () => {
    const handlers = makeHandlers();
    const chat = openChatSocket('thread-1', handlers, Ctor);

    chat.approvalResponse('int-1', [{ tool_call_id: 'call-1', decision: 'approve' }]);

    expect(latestSocket().sent).toEqual([
      JSON.stringify({
        type: 'approval_response',
        interrupt_id: 'int-1',
        decisions: [{ tool_call_id: 'call-1', decision: 'approve' }],
      }),
    ]);
  });
});

describe('openChatSocket — reconnect behavior', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('reconnects with 1s/2s/4s backoff when the socket drops outside an in-flight turn', () => {
    const handlers = makeHandlers();
    openChatSocket('thread-1', handlers, Ctor);

    expect(FakeWebSocket.instances).toHaveLength(1);

    // No turn in flight (fresh connect) -> drop should schedule a reconnect.
    latestSocket().drop();
    expect(FakeWebSocket.instances).toHaveLength(1);

    jest.advanceTimersByTime(1000);
    expect(FakeWebSocket.instances).toHaveLength(2);

    latestSocket().drop();
    jest.advanceTimersByTime(2000);
    expect(FakeWebSocket.instances).toHaveLength(3);

    latestSocket().drop();
    jest.advanceTimersByTime(4000);
    expect(FakeWebSocket.instances).toHaveLength(4);

    // 3 reconnect attempts used up: a further drop should NOT reconnect
    // again, and should surface an error instead.
    latestSocket().drop();
    jest.advanceTimersByTime(10000);
    expect(FakeWebSocket.instances).toHaveLength(4);
    expect(handlers.onError).toHaveBeenCalled();
  });

  it('does NOT reconnect if the socket drops mid-turn, and surfaces onError instead', () => {
    const handlers = makeHandlers();
    openChatSocket('thread-1', handlers, Ctor);

    latestSocket().emit({ type: 'turn_start' });
    latestSocket().drop();

    jest.advanceTimersByTime(10000);

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(handlers.onError).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'error', message: expect.stringContaining('turn') }),
    );
  });

  it(`close code ${WS_CLOSE_NOT_FOUND} reports not-found and never reconnects`, () => {
    const handlers = { ...makeHandlers(), onNotFound: jest.fn(), onConnectionStateChange: jest.fn() };
    openChatSocket('thread-1', handlers, Ctor);

    latestSocket().open();
    latestSocket().drop(WS_CLOSE_NOT_FOUND);
    jest.advanceTimersByTime(10000);

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(handlers.onNotFound).toHaveBeenCalledTimes(1);
    expect(handlers.onError).not.toHaveBeenCalled();
    expect(handlers.onConnectionStateChange).toHaveBeenLastCalledWith('closed');
  });

  it('close() prevents any pending reconnect from firing', () => {
    const handlers = makeHandlers();
    const chat = openChatSocket('thread-1', handlers, Ctor);

    latestSocket().drop();
    chat.close();

    jest.advanceTimersByTime(10000);

    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});

describe('openChatSocket — auth', () => {
  const originalFetch = global.fetch;
  const listener = jest.fn();
  let unsubscribe: () => void = () => {};

  function statusFetch(authenticated: boolean) {
    const fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => ({ setup_required: false, authenticated }),
    });
    global.fetch = fetchMock as unknown as typeof fetch;
    return fetchMock;
  }

  const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

  beforeEach(() => {
    listener.mockReset();
    unsubscribe = onUnauthorized(listener);
  });

  afterEach(() => {
    unsubscribe();
    setSessionToken(null);
    global.fetch = originalFetch;
  });

  it('passes the native bearer token as a WebSocket header', () => {
    setSessionToken('hs_tok');
    openChatSocket('thread-1', makeHandlers(), Ctor).close();

    expect(latestSocket().options).toEqual({ headers: { Authorization: 'Bearer hs_tok' } });
  });

  it('passes no options without a token (browser WebSocket; cookie auth)', () => {
    openChatSocket('thread-1', makeHandlers(), Ctor).close();

    expect(latestSocket().options).toBeUndefined();
  });

  it('a socket rejected before opening probes the session and signs out when it is gone', async () => {
    const fetchMock = statusFetch(false);
    const chat = openChatSocket('thread-1', makeHandlers(), Ctor);

    latestSocket().drop(1006);
    await flush();
    chat.close();

    expect(fetchMock.mock.calls[0][0]).toContain('/api/auth/status');
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('a pre-open failure with a valid session does not sign out', async () => {
    statusFetch(true);
    const chat = openChatSocket('thread-1', makeHandlers(), Ctor);

    latestSocket().drop(1006);
    await flush();
    chat.close();

    expect(listener).not.toHaveBeenCalled();
  });

  it('an ordinary drop after opening does not probe', async () => {
    const fetchMock = statusFetch(false);
    const chat = openChatSocket('thread-1', makeHandlers(), Ctor);

    latestSocket().open();
    latestSocket().drop(1001);
    await flush();
    chat.close();

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it(`close code ${WS_CLOSE_UNAUTHORIZED} after opening probes the session`, async () => {
    statusFetch(false);
    const chat = openChatSocket('thread-1', makeHandlers(), Ctor);

    latestSocket().open();
    latestSocket().drop(WS_CLOSE_UNAUTHORIZED);
    await flush();
    chat.close();

    expect(listener).toHaveBeenCalledTimes(1);
  });
});
