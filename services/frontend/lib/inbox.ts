import { useEffect, useState } from 'react';

import { apiFetch } from './api';

/**
 * The routine-run inbox (M17-05, `GET /api/inbox`, `POST /api/inbox/read`
 * in `services/agent-server/app/api/routines.py`): the user's ended and
 * paused routine runs, newest first. A run is unread again whenever its
 * status changes. The unread count is shared, so the Home tab badge
 * follows reads made on Home.
 */

export type RunStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'timed_out'
  | 'missed'
  | 'waiting_approval'
  | 'expired';

export interface InboxItem {
  id: string;
  routine_id: string;
  routine_name: string;
  trigger: 'schedule' | 'manual';
  status: RunStatus;
  detail: string | null;
  thread_id: string | null;
  due_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  unread: boolean;
}

export interface Inbox {
  unread: number;
  items: InboxItem[];
}

let unread = 0;
const listeners = new Set<(count: number) => void>();

function publish(inbox: Inbox): Inbox {
  unread = inbox.unread;
  listeners.forEach((listener) => listener(unread));
  return inbox;
}

export async function getInbox(): Promise<Inbox> {
  return publish(await apiFetch<Inbox>('/api/inbox'));
}

/** Marks these runs (all, without `runIds`) read. */
export async function markInboxRead(runIds?: string[]): Promise<Inbox> {
  const body = runIds === undefined ? {} : { run_ids: runIds };
  return publish(
    await apiFetch<Inbox>('/api/inbox/read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  );
}

export const INBOX_POLL_MS = 60_000;

/** The unread count, refreshed every `pollMs` and on every read. */
export function useInboxUnread(pollMs: number = INBOX_POLL_MS): number {
  const [count, setCount] = useState(unread);
  useEffect(() => {
    listeners.add(setCount);
    const refresh = () => {
      getInbox().catch(() => undefined);
    };
    refresh();
    const timer = setInterval(refresh, pollMs);
    return () => {
      listeners.delete(setCount);
      clearInterval(timer);
    };
  }, [pollMs]);
  return count;
}

const STATUS_LABELS: Record<RunStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  succeeded: 'Finished',
  failed: 'Failed',
  timed_out: 'Timed out',
  missed: 'Missed',
  waiting_approval: 'Needs approval',
  expired: 'Approval expired',
};

export function runStatusLabel(status: RunStatus): string {
  return STATUS_LABELS[status] ?? status;
}
