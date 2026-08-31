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

export interface UseChatResult {
  items: ChatItem[];
  sendMessage: (text: string) => void;
  busy: boolean;
  connectionState: ChatConnectionState;
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
 */
export function useChat(threadId: string, WebSocketImpl?: WebSocketCtor): UseChatResult {
  const [items, setItems] = useState<ChatItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [connectionState, setConnectionState] = useState<ChatConnectionState>('connecting');

  // Id of the assistant item currently receiving `token` frames, or `null`
  // if the next `token` should start a fresh item (right after connect,
  // after a `tool_start`, or after a `turn_end`/`error`).
  const currentAssistantIdRef = useRef<string | null>(null);
  const socketRef = useRef<ChatSocket | null>(null);

  useEffect(() => {
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
  }, [threadId]);

  const sendMessage = useCallback((text: string) => {
    setItems((prev) => [...prev, { id: makeId('user'), kind: 'user', text }]);
    setBusy(true);
    socketRef.current?.send(text);
  }, []);

  return { items, sendMessage, busy, connectionState };
}
