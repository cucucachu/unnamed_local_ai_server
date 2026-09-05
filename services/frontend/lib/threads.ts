import { apiFetch } from './api';

/**
 * Typed client for the "Reference: Shared Conventions & Contracts" issue
 * (#34), §5 "Threads" REST endpoints — implemented server-side in M3-02
 * (`services/agent-server/app/api/chat.py`). Do not deviate from these
 * shapes; mirrors that module's Pydantic DTOs (`ThreadOut`, `MessageOut`,
 * `ToolCallOut`) field-for-field.
 */

export interface Thread {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

export interface ThreadMessage {
  id: string;
  role: 'user' | 'assistant' | 'tool';
  content: string;
  tool_name: string | null;
  tool_calls: ToolCall[] | null;
  /** M8-04: on `tool` rows, the paired assistant `tool_calls[].id`. */
  tool_call_id?: string | null;
  /** M9-02: on the final assistant row of a turn that has persisted stats. */
  turn?: { status: 'completed' | 'cancelled' | 'awaiting_approval'; duration_ms: number } | null;
}

/** M8-03: one pending mutating tool call awaiting a human decision — same
 * shape as `lib/chatSocket.ts`'s `PendingApprovalAction` (the `approval_request`
 * frame's own `actions[]` entries), duplicated here rather than imported so
 * this module (a plain REST client) doesn't need to depend on the WS
 * module for a shared type. */
export interface PendingApprovalAction {
  tool_call_id: string;
  name: string;
  category: 'file' | 'exec' | 'plan' | 'web' | 'other';
  args: Record<string, unknown>;
  description: string;
}

export interface PendingApproval {
  interrupt_id: string;
  actions: PendingApprovalAction[];
}

export interface ThreadState {
  pending_approval: PendingApproval | null;
}

/** `POST /api/threads` — `title` omitted/`undefined` lets the server default
 * it to `"New chat"` (see `CreateThreadBody.title: str | None = None`). */
export async function createThread(title?: string): Promise<Thread> {
  return apiFetch<Thread>('/api/threads', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: title ?? null }),
  });
}

/** `GET /api/threads` — ordered by `updated_at` desc (server-side sort, not re-sorted here). */
export async function listThreads(): Promise<Thread[]> {
  return apiFetch<Thread[]>('/api/threads');
}

/** `GET /api/threads/{id}/messages` — full checkpointed history, oldest first. */
export async function getThreadMessages(threadId: string): Promise<ThreadMessage[]> {
  return apiFetch<ThreadMessage[]>(`/api/threads/${encodeURIComponent(threadId)}/messages`);
}

/** `GET /api/threads/{id}/state` (M8-03) — `{"pending_approval": {...} | null}`.
 * `useChat` calls this once after history hydration on (re)connect so a
 * pending approval (from before a page reload / reconnect) is restored. */
export async function getThreadState(threadId: string): Promise<ThreadState> {
  return apiFetch<ThreadState>(`/api/threads/${encodeURIComponent(threadId)}/state`);
}

/** `DELETE /api/threads/{id}` — `204 No Content`; `apiFetch`'s `response.json()` call throws on
 * the empty body, which its own `try/catch` already treats as `body = undefined` — so this
 * resolves fine typed as `void` with no special-casing needed here. */
export async function deleteThread(threadId: string): Promise<void> {
  await apiFetch<void>(`/api/threads/${encodeURIComponent(threadId)}`, { method: 'DELETE' });
}
