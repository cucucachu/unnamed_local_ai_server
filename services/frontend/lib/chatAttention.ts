import { useEffect, useState } from 'react';

import { listThreads, markThreadRead, type Thread } from './threads';

/**
 * How many chats need the user (M17-10): waiting on an approval or unread.
 * The Chat tab badge shows it. It's shared, so the chats list's own loads
 * and opening a chat update the badge at once.
 */

const waiting = new Set<string>();
const unread = new Set<string>();
const listeners = new Set<(count: number) => void>();

function count(): number {
  return new Set([...waiting, ...unread]).size;
}

function notify(): void {
  const n = count();
  listeners.forEach((listener) => listener(n));
}

/** Record a fresh `GET /api/threads`. */
export function publishThreads(threads: Thread[]): void {
  waiting.clear();
  unread.clear();
  for (const thread of threads) {
    if (thread.needs_approval) waiting.add(thread.id);
    if (thread.unread) unread.add(thread.id);
  }
  notify();
}

/** The user is looking at this chat. */
export async function readThread(threadId: string): Promise<void> {
  if (unread.delete(threadId)) notify();
  await markThreadRead(threadId);
}

export const ATTENTION_POLL_MS = 60_000;

/** The count, refreshed every `pollMs` and on every list load or read. */
export function useChatAttention(pollMs: number = ATTENTION_POLL_MS): number {
  const [n, setN] = useState(count);
  useEffect(() => {
    listeners.add(setN);
    const refresh = () => {
      listThreads().then(publishThreads, () => undefined);
    };
    refresh();
    const timer = setInterval(refresh, pollMs);
    return () => {
      listeners.delete(setN);
      clearInterval(timer);
    };
  }, [pollMs]);
  return n;
}
