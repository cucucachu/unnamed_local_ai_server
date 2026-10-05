import { probeSession, wsUrl } from './api';
import { authHeaders } from './session';

/**
 * Typed client for the `/ws/chat/{thread_id}` WS contract — "Reference:
 * Shared Conventions & Contracts" issue (#34), §6. Do not deviate from these
 * frame shapes; M2-06's chat screen imports these types directly.
 */

export type ToolCategory = 'file' | 'exec' | 'plan' | 'web' | 'app' | 'other';
export type ToolStatus = 'success' | 'error';

export interface TurnStartFrame {
  type: 'turn_start';
  /** M17-01: set when this socket connected to a turn already running; the
   * frames it has sent so far follow. */
  replay?: boolean;
  /** M17-01: on a replay, the message that started the turn (absent for an
   * approval resume). */
  user_message?: { id: string; content: string };
}

export interface TokenFrame {
  type: 'token';
  content: string;
}

/** M8-07: one streamed thought-delta. Not persisted to history. */
export interface ReasoningFrame {
  type: 'reasoning';
  content: string;
}

export interface ToolStartFrame {
  type: 'tool_start';
  tool_call_id: string;
  name: string;
  category: ToolCategory;
  args: Record<string, unknown>;
}

export interface ToolEndFrame {
  type: 'tool_end';
  tool_call_id: string;
  name: string;
  status: ToolStatus;
  result_preview: string;
}

/** M8-03: one pending mutating tool call awaiting a human decision — a row
 * in an `approval_request` frame's `actions`, or in `GET
 * /api/threads/{id}/state`'s `pending_approval.actions`. */
export interface PendingApprovalAction {
  tool_call_id: string;
  name: string;
  category: ToolCategory;
  args: Record<string, unknown>;
  description: string;
}

/** M8-03: emitted instead of a normal completion when the turn ends with
 * one or more mutating tool calls paused for human approval — ALWAYS
 * immediately followed by `turn_end {"status": "awaiting_approval"}`. */
export interface ApprovalRequestFrame {
  type: 'approval_request';
  interrupt_id: string;
  actions: PendingApprovalAction[];
}

/** `status` (M8-01/M8-03): `"completed"` for a normal finish, `"cancelled"`
 * when the turn was stopped early by a client `cancel` frame,
 * `"awaiting_approval"` when the turn paused on a pending `approval_request`
 * (see `ApprovalRequestFrame` above — always the immediately preceding
 * frame in that case). */
export type TurnEndStatus = 'completed' | 'cancelled' | 'awaiting_approval';

export interface TurnEndFrame {
  type: 'turn_end';
  status: TurnEndStatus;
  /** M9-02: elapsed milliseconds since this turn's `turn_start`. */
  duration_ms?: number;
}

export interface ErrorFrame {
  type: 'error';
  message: string;
}

/** Discriminated union of every server -> client frame shape. */
export type ServerFrame =
  | TurnStartFrame
  | TokenFrame
  | ReasoningFrame
  | ToolStartFrame
  | ToolEndFrame
  | ApprovalRequestFrame
  | TurnEndFrame
  | ErrorFrame;

/** M8-04/M8-05: how an edit/resend/regenerate should rewrite history. */
export type EditMode = 'truncate' | 'fork';

/** Optional fields on an outbound `user_message` (M8-04). */
export interface SendUserMessageOptions {
  /** Drop checkpointed messages from this user-message id onward, then run. */
  replaceFromMessageId?: string;
  /** Defaults server-side to `SettingsStore.edit_mode_default` when omitted. */
  mode?: EditMode;
  /** Client-supplied LangChain message id so the local bubble is addressable
   * in the same session (server falls back to `uuid4()` when omitted). */
  id?: string;
}

/** Client -> server frames. */
export interface UserMessageFrame {
  type: 'user_message';
  content: string;
  replace_from_message_id?: string;
  mode?: EditMode;
  id?: string;
}

/** M8-01: cancels the in-flight turn. Only meaningful while a turn is in
 * flight — a no-op server-side otherwise (see `chat_ws.py`). M8-03: while
 * AWAITING APPROVAL instead, this rejects every pending action (message
 * "The user cancelled.") rather than being a no-op — see `chat_ws.py`. */
export interface CancelFrame {
  type: 'cancel';
}

/** M8-03: one decision for one pending `tool_call_id`, sent as part of an
 * `ApprovalResponseFrame`. */
export interface ApprovalDecision {
  tool_call_id: string;
  decision: 'approve' | 'reject';
}

/** M8-03: resumes a turn paused on `interrupt_id` (must match the pending
 * `approval_request`'s own `interrupt_id`) with one decision per pending
 * `tool_call_id`. Only valid while awaiting approval — see `chat_ws.py`. */
export interface ApprovalResponseFrame {
  type: 'approval_response';
  interrupt_id: string;
  decisions: ApprovalDecision[];
}

/** Connection lifecycle states a UI can render directly (e.g. a "connecting…"
 * pill). `closed` covers both an explicit client-initiated `close()` and a
 * terminal disconnect (thread not found, or reconnect attempts exhausted) —
 * there's no automatic recovery from `closed` short of `reconnectNow()`, so
 * a UI doesn't need to distinguish them beyond "not connected, not
 * retrying". */
export type ChatConnectionState = 'connecting' | 'open' | 'reconnecting' | 'closed';

export interface ChatSocketHandlers {
  onTurnStart?: (frame: TurnStartFrame) => void;
  onToken?: (frame: TokenFrame) => void;
  onReasoning?: (frame: ReasoningFrame) => void;
  onToolStart?: (frame: ToolStartFrame) => void;
  onToolEnd?: (frame: ToolEndFrame) => void;
  /** M8-03. */
  onApprovalRequest?: (frame: ApprovalRequestFrame) => void;
  onTurnEnd?: (frame: TurnEndFrame) => void;
  onError?: (frame: ErrorFrame) => void;
  /** The server closed with `4404`: the thread doesn't exist or isn't the
   * caller's. Terminal — no reconnect follows. */
  onNotFound?: () => void;
  /** M17-01: awaited before each reconnect, e.g. to re-fetch history so the
   * replay of a turn still running (a drop mid-turn doesn't stop it) lands on
   * top of it. A rejection is ignored. */
  beforeReconnect?: () => Promise<void>;
  /** Optional: fires whenever the socket's own connection lifecycle state
   * changes (independent of any particular frame). Additive — existing
   * callers that don't pass it are unaffected. */
  onConnectionStateChange?: (state: ChatConnectionState) => void;
}

export interface ChatSocket {
  /** Serialize and send a `user_message` frame. `options` (M8-04) add
   * `replace_from_message_id` / `mode` / a client-supplied `id`. */
  send(userMessage: string, options?: SendUserMessageOptions): void;
  /** Serialize and send a `cancel` frame (M8-01) — asks the server to stop
   * the in-flight turn. A no-op server-side if no turn is in flight (M8-03:
   * reject-all if awaiting approval instead — see `CancelFrame`'s doc). */
  cancel(): void;
  /** Serialize and send an `approval_response` frame (M8-03). Only
   * meaningful while awaiting approval on the matching `interruptId`. */
  approvalResponse(interruptId: string, decisions: ApprovalDecision[]): void;
  /** Cleanly close the socket; cancels any pending reconnect attempt. */
  close(): void;
  /** M17-01: reconnect now unless open or connecting (e.g. the app came back
   * to the foreground), resetting the backoff. No-op after `close()` or a
   * `4404`. */
  reconnectNow(): void;
}

/** 1s / 2s / 4s backoff, max 3 reconnect attempts. */
const RECONNECT_DELAYS_MS = [1000, 2000, 4000];

/** Minimal surface of the WebSocket API this module depends on, so tests can
 * inject a fake implementation instead of relying on a real WebSocket
 * (which jest-expo's test environment doesn't provide) being global. */
export interface WebSocketLike {
  send(data: string): void;
  close(code?: number, reason?: string): void;
  onopen: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onclose: ((event: unknown) => void) | null;
}

/** The third argument is React Native's extension (browsers take only
 * `url, protocols`); it's how native sends its bearer token, since a
 * browser-style cookie isn't available there. */
export type WebSocketCtor = new (
  url: string,
  protocols?: string | string[],
  options?: { headers: Record<string, string> },
) => WebSocketLike;

/** Close code an app server may use to say "your session is gone" once the
 * socket is already open. */
export const WS_CLOSE_UNAUTHORIZED = 4401;

/** Close code for "thread not found" — a missing thread and another user's
 * thread are indistinguishable. */
export const WS_CLOSE_NOT_FOUND = 4404;

function defaultWebSocketCtor(): WebSocketCtor {
  return (globalThis as { WebSocket?: WebSocketCtor }).WebSocket as WebSocketCtor;
}

/**
 * Connect a chat WebSocket for `threadId`, dispatching parsed server frames
 * to the matching handler. `WebSocketImpl` is injectable (defaults to the
 * global `WebSocket`) purely for testability.
 */
export function openChatSocket(
  threadId: string,
  handlers: ChatSocketHandlers,
  WebSocketImpl: WebSocketCtor = defaultWebSocketCtor(),
): ChatSocket {
  const url = wsUrl(`/ws/chat/${threadId}`);

  let socket: WebSocketLike | null = null;
  let closedByClient = false;
  let notFound = false;
  let connecting = false;
  let reconnectAttempts = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  // `WebSocket.send` throws while CONNECTING, so frames sent before `onopen`
  // (e.g. the app agent panel's auto-sent `askAgent` prompt) wait here.
  let isOpen = false;
  let outbox: string[] = [];

  function sendFrame(frame: object): void {
    const data = JSON.stringify(frame);
    if (socket && isOpen) socket.send(data);
    else outbox.push(data);
  }

  function clearReconnectTimer(): void {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function dispatch(frame: ServerFrame): void {
    switch (frame.type) {
      case 'turn_start':
        handlers.onTurnStart?.(frame);
        return;
      case 'token':
        handlers.onToken?.(frame);
        return;
      case 'reasoning':
        handlers.onReasoning?.(frame);
        return;
      case 'tool_start':
        handlers.onToolStart?.(frame);
        return;
      case 'tool_end':
        handlers.onToolEnd?.(frame);
        return;
      case 'approval_request':
        handlers.onApprovalRequest?.(frame);
        return;
      case 'turn_end':
        handlers.onTurnEnd?.(frame);
        return;
      case 'error':
        handlers.onError?.(frame);
        return;
      default:
        // Unknown frame `type`: tolerate/ignore rather than throw.
        return;
    }
  }

  function handleMessage(event: { data: unknown }): void {
    if (typeof event.data !== 'string') return;

    let parsed: unknown;
    try {
      parsed = JSON.parse(event.data);
    } catch {
      return;
    }

    if (
      parsed === null ||
      typeof parsed !== 'object' ||
      typeof (parsed as { type?: unknown }).type !== 'string'
    ) {
      return;
    }

    dispatch(parsed as ServerFrame);
  }

  function scheduleReconnect(): void {
    if (closedByClient) return;
    if (reconnectAttempts >= RECONNECT_DELAYS_MS.length) {
      handlers.onConnectionStateChange?.('closed');
      handlers.onError?.({
        type: 'error',
        message: `chat socket disconnected and failed to reconnect after ${RECONNECT_DELAYS_MS.length} attempts`,
      });
      return;
    }
    handlers.onConnectionStateChange?.('reconnecting');
    const delay = RECONNECT_DELAYS_MS[reconnectAttempts];
    reconnectAttempts += 1;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      void reconnect();
    }, delay);
  }

  async function reconnect(): Promise<void> {
    if (!handlers.beforeReconnect) {
      connect();
      return;
    }
    connecting = true;
    try {
      await handlers.beforeReconnect();
    } catch {
      // The connect below fails and retries too if the server is still away.
    }
    if (closedByClient) {
      connecting = false;
      return;
    }
    connect();
  }

  function handleClose(event: unknown, opened: boolean): void {
    if (closedByClient) return;

    if ((event as { code?: unknown } | null)?.code === WS_CLOSE_NOT_FOUND) {
      notFound = true;
      handlers.onConnectionStateChange?.('closed');
      handlers.onNotFound?.();
      return;
    }

    // A rejected upgrade (e.g. `forward_auth` 401) never opens and exposes
    // no status to JS, so ask the platform directly; `probeSession` signs
    // out only if it confirms the session is gone.
    if (!opened || (event as { code?: unknown } | null)?.code === WS_CLOSE_UNAUTHORIZED) {
      void probeSession();
    }

    scheduleReconnect();
  }

  function connect(): void {
    connecting = true;
    handlers.onConnectionStateChange?.(reconnectAttempts > 0 ? 'reconnecting' : 'connecting');
    const headers = authHeaders();
    socket =
      Object.keys(headers).length > 0
        ? new WebSocketImpl(url, undefined, { headers })
        : new WebSocketImpl(url);
    let opened = false;
    const current = socket;
    socket.onopen = () => {
      opened = true;
      isOpen = true;
      connecting = false;
      reconnectAttempts = 0;
      handlers.onConnectionStateChange?.('open');
      const pending = outbox;
      outbox = [];
      for (const data of pending) current.send(data);
    };
    socket.onmessage = handleMessage;
    // Browsers/RN always follow a WebSocket error with a close event, so
    // `onclose` alone owns the reconnect/surface-error decision — handling
    // it in both places would double-fire.
    socket.onerror = () => {};
    socket.onclose = (event) => {
      if (current !== socket) return;
      isOpen = false;
      connecting = false;
      handleClose(event, opened);
    };
  }

  connect();

  return {
    send(userMessage: string, options?: SendUserMessageOptions): void {
      const frame: UserMessageFrame = { type: 'user_message', content: userMessage };
      if (options?.replaceFromMessageId) {
        frame.replace_from_message_id = options.replaceFromMessageId;
      }
      if (options?.mode) {
        frame.mode = options.mode;
      }
      if (options?.id) {
        frame.id = options.id;
      }
      sendFrame(frame);
    },
    cancel(): void {
      const frame: CancelFrame = { type: 'cancel' };
      sendFrame(frame);
    },
    approvalResponse(interruptId: string, decisions: ApprovalDecision[]): void {
      const frame: ApprovalResponseFrame = {
        type: 'approval_response',
        interrupt_id: interruptId,
        decisions,
      };
      sendFrame(frame);
    },
    close(): void {
      closedByClient = true;
      isOpen = false;
      outbox = [];
      clearReconnectTimer();
      socket?.close();
      handlers.onConnectionStateChange?.('closed');
    },
    reconnectNow(): void {
      if (closedByClient || notFound || isOpen || connecting) return;
      clearReconnectTimer();
      reconnectAttempts = 0;
      void reconnect();
    },
  };
}
