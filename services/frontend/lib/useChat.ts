import { useCallback, useEffect, useRef, useState } from 'react';

import {
  openChatSocket,
  type ChatConnectionState,
  type ChatSocket,
  type ErrorFrame,
  type ToolCategory,
  type ToolEndFrame,
  type ToolStartFrame,
  type ToolStatus,
  type WebSocketCtor,
} from './chatSocket';
import { getThreadMessages, type ThreadMessage } from './threads';

let nextItemId = 0;
/** Monotonic id generator — good enough for a single-session client list key
 * (no need for crypto-strength uniqueness, just stable React keys and a way
 * to find "the currently streaming item" by identity). */
function makeId(prefix: string): string {
  nextItemId += 1;
  return `${prefix}-${nextItemId}`;
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
  status: 'running' | ToolStatus;
  args: Record<string, unknown>;
  resultPreview?: string;
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
 *   `toolCallId` has no real value to recover here (there's no live
 *   `tool_end` frame to correlate against for a hydrated row) — judgement
 *   call: fabricate one from the row's own `id` (unique, stable across
 *   re-renders of the same hydration), documented per the ticket's request.
 */
export function mapHistoryToItems(messages: ThreadMessage[]): ChatItem[] {
  const items: ChatItem[] = [];
  for (const message of messages) {
    if (message.role === 'user') {
      items.push({ id: message.id, kind: 'user', text: message.content });
      continue;
    }
    if (message.role === 'tool') {
      items.push({
        id: message.id,
        kind: 'tool',
        toolCallId: message.id,
        name: message.tool_name ?? '',
        category: categoryForToolName(message.tool_name ?? ''),
        status: 'success',
        // §5's `tool` DTO row has no `args` field at all (only the paired
        // `assistant` row's `tool_calls[].args` carries them, and that row
        // exposes no id shared with this `tool` row to correlate against —
        // `ToolCallOut` has its own `id`, but `_normalize_message` doesn't
        // thread the matching `ToolMessage.tool_call_id` into `MessageOut`
        // for `tool`-role rows). Rather than guess at positional pairing,
        // hydrated tool cards simply show no args (the expandable detail
        // panel still shows the real `resultPreview`).
        args: {},
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
  sendMessage: (text: string) => void;
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
export function useChat(threadId: string, WebSocketImpl?: WebSocketCtor): UseChatResult {
  const [items, setItems] = useState<ChatItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [connectionState, setConnectionState] = useState<ChatConnectionState>('connecting');
  const [hydrationState, setHydrationState] = useState<HydrationState>('loading');
  // Bumped by `retryHydration` to re-trigger the hydration effect below
  // without needing `threadId` itself to change.
  const [hydrationAttempt, setHydrationAttempt] = useState(0);

  // Id of the assistant item currently receiving `token` frames, or `null`
  // if the next `token` should start a fresh item (right after connect,
  // after a `tool_start`, or after a `turn_end`/`error`).
  const currentAssistantIdRef = useRef<string | null>(null);
  const socketRef = useRef<ChatSocket | null>(null);

  // History hydration — runs before the socket-opening effect below ever
  // fires (that effect's own `hydrationState !== 'done'` guard is what
  // enforces "socket not opened" while loading/erroring, per spec). Resets
  // `hydrationState` to `'loading'` (and clears any stale items) whenever
  // `threadId` changes or `retryHydration` bumps `hydrationAttempt`.
  useEffect(() => {
    let cancelled = false;
    setHydrationState('loading');
    setItems([]);

    getThreadMessages(threadId)
      .then((messages) => {
        if (cancelled) return;
        setItems(mapHistoryToItems(messages));
        setHydrationState('done');
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
        onTurnEnd: () => {
          const id = currentAssistantIdRef.current;
          currentAssistantIdRef.current = null;
          setBusy(false);
          if (id === null) return;
          setItems((prev) =>
            prev.map((item) => (item.id === id && item.kind === 'assistant' ? { ...item, streaming: false } : item)),
          );
        },
        onError: (frame: ErrorFrame) => {
          const streamingId = currentAssistantIdRef.current;
          currentAssistantIdRef.current = null;
          setBusy(false);
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

  const sendMessage = useCallback((text: string) => {
    setItems((prev) => [...prev, { id: makeId('user'), kind: 'user', text }]);
    setBusy(true);
    socketRef.current?.send(text);
  }, []);

  return { items, sendMessage, busy, connectionState, hydrationState, retryHydration };
}
