import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  openChatSocket,
  type ApprovalDecision,
  type ApprovalRequestFrame,
  type ChatConnectionState,
  type ChatSocket,
  type ErrorFrame,
  type SendUserMessageOptions,
  type ToolCategory,
  type ToolEndFrame,
  type ToolStartFrame,
  type ToolStatus,
  type TurnEndFrame,
  type WebSocketCtor,
} from './chatSocket';
import {
  extractTurnMetaFromHistory,
  groupItemsIntoTurns,
  type ChatTurn,
  type ChatTurnMeta,
  type ChatTurnStatus,
  ORPHAN_TURN_KEY,
} from './chatTurns';
import { getThreadMessages, getThreadState, type ThreadMessage } from './threads';

export type { ChatTurn, ChatTurnMeta, ChatTurnStatus };
export { extractTurnMetaFromHistory, groupItemsIntoTurns };

export type { SendUserMessageOptions };

let nextItemId = 0;
/** Monotonic id generator — good enough for a single-session client list key
 * (no need for crypto-strength uniqueness, just stable React keys and a way
 * to find "the currently streaming item" by identity). */
function makeId(prefix: string): string {
  nextItemId += 1;
  return `${prefix}-${nextItemId}`;
}

/** Stable id for a newly-sent user bubble (M8-04). Prefer `crypto.randomUUID`
 * so the same value can be sent on the `user_message` frame and stored as
 * the LangChain `HumanMessage.id` — Edit/Resend in the same session can
 * then address the bubble without a history refetch. Falls back to
 * `makeId` in environments without `randomUUID`. */
function newUserMessageId(): string {
  const cryptoObj = globalThis.crypto;
  if (cryptoObj && typeof cryptoObj.randomUUID === 'function') {
    return cryptoObj.randomUUID();
  }
  return makeId('user');
}

export interface ChatUserItem {
  id: string;
  kind: 'user';
  text: string;
}

export interface ChatAssistantItem {
  id: string;
  kind: 'assistant';
  text: string;
  streaming: boolean;
  /** M8-01: `true` if this item was cut short by a client `cancel` — the
   * server-side turn ended early with `turn_end {"status": "cancelled"}`.
   * Never set on a hydrated (history) item — only a live turn can be
   * stopped. UI shows a small "Stopped" caption for these. */
  stopped?: boolean;
}

export interface ChatToolItem {
  id: string;
  kind: 'tool';
  /** The frame's `tool_call_id` — used to find/update this item when the
   * matching `tool_end` frame arrives. Kept distinct from `id` (the React
   * list key) since, in principle, `tool_call_id` values aren't guaranteed
   * unique for the item's whole lifetime the way a dedicated list-key is. */
  toolCallId: string;
  name: string;
  category: ToolCategory;
  /** `'rejected'` (M8-03): client-only status for an action a human
   * rejected via the `ApprovalCard` — synthesized locally (see `useChat`'s
   * `respondToApproval`) since a rejected tool call never actually runs,
   * so no real `tool_start`/`tool_end` frame ever arrives for it. */
  status: 'running' | ToolStatus | 'rejected';
  args: Record<string, unknown>;
  resultPreview?: string;
}

/** M8-03: one pending mutating tool call awaiting a human decision —
 * `useChat`'s camelCase mirror of `chatSocket.ts`'s `PendingApprovalAction`
 * (an `approval_request` frame's `actions[]` entry, or `GET
 * /api/threads/{id}/state`'s `pending_approval.actions[]` entry). */
export interface PendingApprovalAction {
  toolCallId: string;
  name: string;
  category: ToolCategory;
  args: Record<string, unknown>;
  description: string;
}

export interface PendingApproval {
  interruptId: string;
  actions: PendingApprovalAction[];
}

/** Not in the ticket's literal `ChatItem` sketch — added to represent an
 * `error` frame (and the socket's own terminal-disconnect errors) as a
 * renderable list item, per the ticket's "use your judgement" note. A
 * dedicated variant (rather than reusing `assistant` with a flag) keeps
 * `assistant.text` semantics simple (always real streamed model text) and
 * lets the UI style errors distinctly. */
export interface ChatErrorItem {
  id: string;
  kind: 'error';
  message: string;
}

export type ChatItem = ChatUserItem | ChatAssistantItem | ChatToolItem | ChatErrorItem;

/**
 * `'loading'` while `GET /api/threads/{id}/messages` (M3-02/M3-04's history
 * hydration call) is in flight; `'error'` if it failed (socket is never
 * opened in this state — see the module doc below); `'done'` once history
 * has been mapped into `items` and the live socket has been opened.
 */
export type HydrationState = 'loading' | 'error' | 'done';

/** Mirrors `chat_ws.py`'s `_TOOL_CATEGORY_BY_NAME` (Conventions & Contracts
 * §6 category assignment). The §5 history DTO (`ThreadMessage`, from
 * `lib/threads.ts`) has no `category` field of its own — only the live WS
 * `tool_start` frame carries one — so a hydrated `ChatToolItem` (which
 * needs a `category` to pick its icon, same as a live one) has to re-derive
 * it from the tool name client-side. Kept in sync with the server's table
 * by hand; a mismatch here only affects which icon a hydrated tool card
 * shows, never correctness of the transcript itself. */
const TOOL_CATEGORY_BY_NAME: Record<string, ToolCategory> = {
  ls: 'file',
  read_file: 'file',
  write_file: 'file',
  edit_file: 'file',
  glob: 'file',
  grep: 'file',
  delete: 'file',
  execute_code: 'exec',
  write_todos: 'plan',
  task: 'plan',
  web_search: 'web',
  web_fetch: 'web',
};

function categoryForToolName(name: string): ToolCategory {
  return TOOL_CATEGORY_BY_NAME[name] ?? 'other';
}

/**
 * Maps `GET /api/threads/{id}/messages` history (§5 `MessageOut[]`, oldest
 * first) to the same `ChatItem[]` shape a live socket session builds up.
 * Mapping rules (spec, M3-04):
 *
 * - `user` rows -> `ChatUserItem` directly.
 * - `assistant` rows with non-empty `content` -> `ChatAssistantItem`
 *   (`streaming: false` — history is never mid-stream).
 * - `assistant` rows with EMPTY `content` and at least one `tool_calls`
 *   entry -> nothing (the immediately-following `tool` row's `ChatToolItem`
 *   already represents that turn's tool call; rendering an empty assistant
 *   bubble too would just be a blank artifact — this mirrors
 *   `onToolStart`'s live-socket "drop the empty pre-tool bubble" behavior
 *   above).
 * - `assistant` rows with empty content and NO `tool_calls` fall through to
 *   the same `ChatAssistantItem` mapping as any other assistant row (an
 *   empty bubble in that case reflects a real, if odd, stored turn rather
 *   than a mapping artifact — nothing in the spec says to hide it).
 * - `tool` rows -> `ChatToolItem`, `status: 'success'` (per spec — history
 *   has no separate error signal; a tool that errored still produced a
 *   normal stored `tool` message) and `resultPreview` set to the row's
 *   `content` (the full stored tool output, reused as the "preview" — the
 *   §5 DTO doesn't distinguish a separate preview vs. full-content field
 *   the way the live `tool_end` frame's `result_preview` truncation does).
 *   `toolCallId` is the row's `tool_call_id` when present (M8-04 — the
 *   paired assistant `tool_calls[].id`), else the row's own `id` (unique,
 *   stable across re-renders of the same hydration). `args` are recovered
 *   from that paired assistant row's `tool_calls` via `tool_call_id`
 *   (fixes the pre-M8-04 `args: {}` gap).
 */
export function mapHistoryToItems(messages: ThreadMessage[]): ChatItem[] {
  const argsByToolCallId = new Map<string, Record<string, unknown>>();
  for (const message of messages) {
    if (message.role !== 'assistant' || !message.tool_calls) continue;
    for (const toolCall of message.tool_calls) {
      if (toolCall.id) argsByToolCallId.set(toolCall.id, toolCall.args);
    }
  }

  const items: ChatItem[] = [];
  for (const message of messages) {
    if (message.role === 'user') {
      items.push({ id: message.id, kind: 'user', text: message.content });
      continue;
    }
    if (message.role === 'tool') {
      const toolCallId = message.tool_call_id || message.id;
      items.push({
        id: message.id,
        kind: 'tool',
        toolCallId,
        name: message.tool_name ?? '',
        category: categoryForToolName(message.tool_name ?? ''),
        status: 'success',
        args: argsByToolCallId.get(toolCallId) ?? {},
        resultPreview: message.content,
      });
      continue;
    }
    // role === 'assistant'
    const hasToolCalls = Boolean(message.tool_calls && message.tool_calls.length > 0);
    if (message.content === '' && hasToolCalls) continue;
    items.push({ id: message.id, kind: 'assistant', text: message.content, streaming: false });
  }
  return items;
}

export interface UseChatResult {
  items: ChatItem[];
  /** M9-02: `items` grouped into turns for the activity-panel UI. */
  turns: ChatTurn[];
  sendMessage: (text: string, options?: SendUserMessageOptions) => void;
  /** M8-01: sends a `cancel` frame for the in-flight turn (a no-op
   * server-side if `busy` is already `false`). M8-03: while
   * `pendingApproval` is set, this rejects every pending action instead
   * (see `chat_ws.py`'s "cancel while awaiting approval" handling). */
  stopTurn: () => void;
  busy: boolean;
  connectionState: ChatConnectionState;
  /** `'loading'` | `'error'` | `'done'` — see `HydrationState`. Gate any
   * message-composer UI on `'done'` (spec: "show a spinner until hydration
   * completes"). */
  hydrationState: HydrationState;
  /** Re-runs history hydration from scratch after a failed attempt (spec:
   * "error banner with retry"). No-op while a hydration attempt is already
   * in flight. */
  retryHydration: () => void;
  /** M8-03: the currently pending approval (from a live `approval_request`
   * frame OR restored via `GET /api/threads/{id}/state` right after
   * hydration on connect/reconnect), or `null` if none. Gate the
   * `ApprovalCard`'s visibility and the composer's disabled state on this
   * being non-`null`. */
  pendingApproval: PendingApproval | null;
  /** M8-03: sends one decision per pending action (order doesn't matter —
   * matched to `pendingApproval.actions` by `toolCallId`; every pending
   * `toolCallId` must have an entry, or this throws) and immediately
   * clears `pendingApproval` + disables further responses for this
   * approval (avoids a double-submit race from a fast double-tap). Any
   * rejected action gets a synthesized `ChatToolItem` with `status:
   * 'rejected'` right away, since no real `tool_start`/`tool_end` frame
   * will ever arrive for it. */
  respondToApproval: (decisions: ApprovalDecision[]) => void;
}

/**
 * Drives a `ChatItem[]` list off the real `/ws/chat/{threadId}` frame stream
 * (via `lib/chatSocket.ts`'s `openChatSocket`). See the module's ticket
 * report for the full frame -> state-transition mapping; in short:
 *
 * - `turn_start` opens a new streaming assistant item.
 * - `token` appends to "the current streaming item" (tracked via
 *   `currentAssistantIdRef` below, not a list search — O(1) per token).
 * - `tool_start` appends a running tool item AND clears
 *   `currentAssistantIdRef`, so any tokens that arrive after it start a
 *   fresh assistant item (the "split" behavior the ticket calls out).
 * - `tool_end` finds+patches the matching tool item by `tool_call_id`.
 * - `turn_end` closes out the current streaming item (if any) and clears
 *   `busy`.
 * - `error` appends a `ChatErrorItem` and clears `busy`.
 *
 * `WebSocketImpl` is exposed purely for test injection, mirroring
 * `openChatSocket`'s own `WebSocketCtor` parameter (see
 * `lib/__tests__/chatSocket.test.ts`'s `FakeWebSocket` pattern) — tests for
 * this hook reuse the same fake instead of duplicating it.
 *
 * M3-04 adds a history-hydration phase that runs BEFORE the socket ever
 * opens: `GET /api/threads/{threadId}/messages` is fetched and mapped via
 * `mapHistoryToItems` into the hook's `items` (see `hydrationState`,
 * `retryHydration` on `UseChatResult`, and the socket effect's leading
 * `hydrationState !== 'done'` guard below).
 */
/** M8-03: maps a wire `approval_request`/`GET .../state` action (snake_case)
 * to `useChat`'s own camelCase `PendingApprovalAction`. */
function toPendingApprovalAction(action: {
  tool_call_id: string;
  name: string;
  category: ToolCategory;
  args: Record<string, unknown>;
  description: string;
}): PendingApprovalAction {
  return {
    toolCallId: action.tool_call_id,
    name: action.name,
    category: action.category,
    args: action.args,
    description: action.description,
  };
}

export function useChat(threadId: string, WebSocketImpl?: WebSocketCtor): UseChatResult {
  const [items, setItems] = useState<ChatItem[]>([]);
  const [turnMetas, setTurnMetas] = useState<Record<string, ChatTurnMeta>>({});
  const [busy, setBusy] = useState(false);
  const [connectionState, setConnectionState] = useState<ChatConnectionState>('connecting');
  const [hydrationState, setHydrationState] = useState<HydrationState>('loading');
  const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null);
  // Bumped by `retryHydration` to re-trigger the hydration effect below
  // without needing `threadId` itself to change.
  const [hydrationAttempt, setHydrationAttempt] = useState(0);
  const currentTurnUserIdRef = useRef<string | null>(null);
  const turnStartedAtRef = useRef<number | null>(null);

  // Id of the assistant item currently receiving `token` frames, or `null`
  // if the next `token` should start a fresh item (right after connect,
  // after a `tool_start`, or after a `turn_end`/`error`).
  const currentAssistantIdRef = useRef<string | null>(null);
  const socketRef = useRef<ChatSocket | null>(null);
  // Mirrors `pendingApproval` for synchronous reads from `respondToApproval`
  // (a plain state closure would risk acting on a stale value if called
  // twice in the same tick — the ref is always current).
  const pendingApprovalRef = useRef<PendingApproval | null>(null);

  // History hydration — runs before the socket-opening effect below ever
  // fires (that effect's own `hydrationState !== 'done'` guard is what
  // enforces "socket not opened" while loading/erroring, per spec). Resets
  // `hydrationState` to `'loading'` (and clears any stale items) whenever
  // `threadId` changes or `retryHydration` bumps `hydrationAttempt`.
  useEffect(() => {
    let cancelled = false;
    setHydrationState('loading');
    setItems([]);
    setTurnMetas({});
    setPendingApproval(null);
    pendingApprovalRef.current = null;
    currentTurnUserIdRef.current = null;
    turnStartedAtRef.current = null;

    getThreadMessages(threadId)
      .then(async (messages) => {
        if (cancelled) return;
        setItems(mapHistoryToItems(messages));
        setTurnMetas(extractTurnMetaFromHistory(messages));

        // M8-03: restore a pending approval left over from before a
        // reconnect/reload. Best-effort relative to hydration itself (a
        // failure here just means no approval card shows up until the next
        // live `approval_request`, not a hydration failure) — but we DO
        // await it before flipping to `'done'` so the composer/card don't
        // flash the idle state for one frame, and so the socket opens
        // with `pendingApproval` already populated.
        try {
          const state = await getThreadState(threadId);
          if (cancelled) return;
          if (state.pending_approval !== null) {
            const restored: PendingApproval = {
              interruptId: state.pending_approval.interrupt_id,
              actions: state.pending_approval.actions.map(toPendingApprovalAction),
            };
            pendingApprovalRef.current = restored;
            setPendingApproval(restored);
          }
        } catch {
          // Best-effort restore only — see comment above.
        }
        if (!cancelled) setHydrationState('done');
      })
      .catch(() => {
        if (cancelled) return;
        setHydrationState('error');
      });

    return () => {
      cancelled = true;
    };
  }, [threadId, hydrationAttempt]);

  const retryHydration = useCallback(() => {
    setHydrationAttempt((n) => n + 1);
  }, []);

  useEffect(() => {
    // Spec: hydration failure -> error banner with retry, socket not
    // opened. Also gates the initial `'loading'` phase — the socket only
    // opens once history has been fetched and mapped into `items`.
    if (hydrationState !== 'done') return;

    currentAssistantIdRef.current = null;

    const socket = openChatSocket(
      threadId,
      {
        onTurnStart: () => {
          turnStartedAtRef.current = Date.now();
          const userId = currentTurnUserIdRef.current ?? ORPHAN_TURN_KEY;
          setTurnMetas((prev) => ({
            ...prev,
            [userId]: { status: 'running', durationMs: prev[userId]?.durationMs },
          }));
          const id = makeId('assistant');
          currentAssistantIdRef.current = id;
          setItems((prev) => [...prev, { id, kind: 'assistant', text: '', streaming: true }]);
        },
        onToken: (frame) => {
          const id = currentAssistantIdRef.current ?? makeId('assistant');
          currentAssistantIdRef.current = id;
          setItems((prev) => {
            const existing = prev.find((item) => item.id === id);
            if (!existing) {
              return [...prev, { id, kind: 'assistant', text: frame.content, streaming: true }];
            }
            return prev.map((item) =>
              item.id === id && item.kind === 'assistant'
                ? { ...item, text: item.text + frame.content }
                : item,
            );
          });
        },
        onToolStart: (frame: ToolStartFrame) => {
          // A tool call always splits the response: subsequent tokens must
          // land in a brand-new assistant item, not this one. Clearing the
          // ref means the *next* `token` (if any) lazily creates that new
          // item — see `onToken` above — rather than creating it here.
          const previousStreamingId = currentAssistantIdRef.current;
          currentAssistantIdRef.current = null;

          const toolItem: ChatToolItem = {
            id: makeId('tool'),
            kind: 'tool',
            toolCallId: frame.tool_call_id,
            name: frame.name,
            category: frame.category,
            status: 'running',
            args: frame.args,
          };

          setItems((prev) => {
            if (previousStreamingId === null) return [...prev, toolItem];
            // The item `turn_start` (or a prior `tool_start`) opened is
            // being interrupted by this tool call: if it never received any
            // tokens, drop it (an empty bubble would be a UI artifact with
            // nothing to show — this is exactly the "tool-only turn" case
            // the ticket calls out); otherwise close it out as finished.
            const withPreviousResolved = prev.flatMap((item) => {
              if (item.id !== previousStreamingId || item.kind !== 'assistant') return [item];
              if (item.text === '') return [];
              return [{ ...item, streaming: false }];
            });
            return [...withPreviousResolved, toolItem];
          });
        },
        onToolEnd: (frame: ToolEndFrame) => {
          setItems((prev) =>
            prev.map((item) =>
              item.kind === 'tool' && item.toolCallId === frame.tool_call_id
                ? { ...item, status: frame.status, resultPreview: frame.result_preview }
                : item,
            ),
          );
        },
        onApprovalRequest: (frame: ApprovalRequestFrame) => {
          const pending: PendingApproval = {
            interruptId: frame.interrupt_id,
            actions: frame.actions.map(toPendingApprovalAction),
          };
          pendingApprovalRef.current = pending;
          setPendingApproval(pending);
        },
        onTurnEnd: (frame: TurnEndFrame) => {
          const id = currentAssistantIdRef.current;
          currentAssistantIdRef.current = null;
          setBusy(false);
          const userId = currentTurnUserIdRef.current ?? ORPHAN_TURN_KEY;
          const durationMs =
            frame.duration_ms ??
            (turnStartedAtRef.current != null ? Date.now() - turnStartedAtRef.current : undefined);
          const status: ChatTurnStatus = frame.status;
          setTurnMetas((prev) => {
            const prior = prev[userId];
            const summed =
              prior?.status === 'awaiting_approval' && prior.durationMs != null && durationMs != null
                ? prior.durationMs + durationMs
                : durationMs;
            return { ...prev, [userId]: { status, durationMs: summed } };
          });
          const stopped = frame.status === 'cancelled';
          if (id === null) return;
          setItems((prev) =>
            prev.map((item) =>
              item.id === id && item.kind === 'assistant'
                ? { ...item, streaming: false, ...(stopped ? { stopped: true } : {}) }
                : item,
            ),
          );
        },
        onError: (frame: ErrorFrame) => {
          const streamingId = currentAssistantIdRef.current;
          currentAssistantIdRef.current = null;
          setBusy(false);
          const userId = currentTurnUserIdRef.current ?? ORPHAN_TURN_KEY;
          const durationMs =
            turnStartedAtRef.current != null ? Date.now() - turnStartedAtRef.current : undefined;
          setTurnMetas((prev) => ({ ...prev, [userId]: { status: 'error', durationMs } }));
          setItems((prev) => {
            // Close out any dangling streaming item first, so an error mid-turn
            // doesn't leave a permanently-"streaming" bubble (cursor forever).
            const closed =
              streamingId === null
                ? prev
                : prev.map((item) =>
                    item.id === streamingId && item.kind === 'assistant' ? { ...item, streaming: false } : item,
                  );
            return [...closed, { id: makeId('error'), kind: 'error', message: frame.message }];
          });
        },
        onConnectionStateChange: setConnectionState,
      },
      WebSocketImpl,
    );

    socketRef.current = socket;
    return () => {
      socket.close();
      socketRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- WebSocketImpl is a test-only override, stable in real usage
  }, [threadId, hydrationState]);

  const sendMessage = useCallback((text: string, options?: SendUserMessageOptions) => {
    const id = newUserMessageId();
    currentTurnUserIdRef.current = id;
    setItems((prev) => {
      let next = prev;
      if (options?.replaceFromMessageId) {
        const cutAt = prev.findIndex((item) => item.id === options.replaceFromMessageId);
        if (cutAt !== -1) {
          const removedUserIds = new Set(
            prev.slice(cutAt).filter((item) => item.kind === 'user').map((item) => item.id),
          );
          setTurnMetas((metas) => {
            const kept: Record<string, ChatTurnMeta> = {};
            for (const [key, value] of Object.entries(metas)) {
              if (!removedUserIds.has(key)) kept[key] = value;
            }
            kept[id] = { status: 'running' };
            return kept;
          });
          next = prev.slice(0, cutAt);
        }
      } else {
        setTurnMetas((prevMetas) => ({ ...prevMetas, [id]: { status: 'running' } }));
      }
      return [...next, { id, kind: 'user', text }];
    });
    setBusy(true);
    socketRef.current?.send(text, { ...options, id });
  }, []);

  const stopTurn = useCallback(() => {
    socketRef.current?.cancel();
  }, []);

  const respondToApproval = useCallback((decisions: ApprovalDecision[]) => {
    const pending = pendingApprovalRef.current;
    if (pending === null) return;

    // Clear immediately (both the ref, read synchronously above, and the
    // state that gates the `ApprovalCard`/composer) so a fast double-tap
    // can't send a second `approval_response` for the same interrupt.
    pendingApprovalRef.current = null;
    setPendingApproval(null);
    setBusy(true);

    // Synthesize a `ChatToolItem` for every REJECTED action right away —
    // a rejected tool call never actually runs, so no real
    // `tool_start`/`tool_end` frame will ever arrive for it (see this
    // hook's `PendingApprovalAction.status` doc). Approved actions get no
    // synthesized item: the resumed turn's real `tool_start`/`tool_end`
    // frames cover them exactly like any other tool call.
    const decisionByToolCallId = new Map(decisions.map((d) => [d.tool_call_id, d.decision]));
    const rejectedItems: ChatToolItem[] = pending.actions
      .filter((action) => decisionByToolCallId.get(action.toolCallId) === 'reject')
      .map((action) => ({
        id: makeId('tool'),
        kind: 'tool',
        toolCallId: action.toolCallId,
        name: action.name,
        category: action.category,
        status: 'rejected',
        args: action.args,
      }));
    if (rejectedItems.length > 0) {
      setItems((prev) => [...prev, ...rejectedItems]);
    }

    socketRef.current?.approvalResponse(pending.interruptId, decisions);
  }, []);

  const turns = useMemo(() => groupItemsIntoTurns(items, turnMetas), [items, turnMetas]);

  return {
    items,
    turns,
    sendMessage,
    stopTurn,
    busy,
    connectionState,
    hydrationState,
    retryHydration,
    pendingApproval,
    respondToApproval,
  };
}
