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

/** `DELETE /api/threads/{id}` — `204 No Content`; `apiFetch`'s `response.json()` call throws on
 * the empty body, which its own `try/catch` already treats as `body = undefined` — so this
 * resolves fine typed as `void` with no special-casing needed here. */
export async function deleteThread(threadId: string): Promise<void> {
  await apiFetch<void>(`/api/threads/${encodeURIComponent(threadId)}`, { method: 'DELETE' });
}
