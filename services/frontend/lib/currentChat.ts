import { useSyncExternalStore } from 'react';
import { Platform } from 'react-native';

/**
 * Which chat the Chat tab shows (M19-02): the last one open this app
 * session, or a new empty chat (`threadId: null`) on a cold launch. On web
 * the session is the browser tab (`sessionStorage`), so a reload keeps the
 * chat and a new tab starts empty. `key` changes whenever the user switches
 * chats, but not when a new chat gets its server id on the first send, so
 * that send isn't remounted away.
 */
export interface CurrentChat {
  threadId: string | null;
  key: number;
}

const STORAGE_KEY = 'homeai.currentChat';

function tabStorage(): Storage | null {
  if (Platform.OS !== 'web') return null;
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
}

let current: CurrentChat = { threadId: tabStorage()?.getItem(STORAGE_KEY) || null, key: 0 };
const listeners = new Set<() => void>();

function set(next: CurrentChat): void {
  current = next;
  const storage = tabStorage();
  if (next.threadId === null) storage?.removeItem(STORAGE_KEY);
  else storage?.setItem(STORAGE_KEY, next.threadId);
  listeners.forEach((listener) => listener());
}

/** Show this chat, or a new empty one for `null`. */
export function openChat(threadId: string | null): void {
  if (threadId !== null && threadId === current.threadId) return;
  set({ threadId, key: current.key + 1 });
}

/** The new chat on screen now exists on the server as `threadId`. */
export function adoptCreatedThread(threadId: string): void {
  set({ threadId, key: current.key });
}

/** Back to a cold launch's empty chat (sign-out). */
export function resetCurrentChat(): void {
  set({ threadId: null, key: current.key + 1 });
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getCurrentChat(): CurrentChat {
  return current;
}

export function useCurrentChat(): CurrentChat {
  return useSyncExternalStore(subscribe, getCurrentChat, getCurrentChat);
}
