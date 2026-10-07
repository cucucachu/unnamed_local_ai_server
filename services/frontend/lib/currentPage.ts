import { useSyncExternalStore } from 'react';
import { Platform } from 'react-native';

/**
 * Which page of the home pager is showing (M19-07): `chat`, `apps` (the
 * first space, Personal) or a space id. A cold launch or sign-in opens Chat;
 * on web the session is the browser tab (`sessionStorage`), like
 * `currentChat.ts`. `jump` changes only when something other than a swipe
 * asks for a page (a link, the page indicator), so the pager knows to
 * scroll there.
 */
export type HomePage = 'chat' | 'apps' | (string & {});

export interface CurrentPage {
  page: HomePage;
  jump: number;
}

const STORAGE_KEY = 'homeai.currentPage';

function tabStorage(): Storage | null {
  if (Platform.OS !== 'web') return null;
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
}

let current: CurrentPage = { page: tabStorage()?.getItem(STORAGE_KEY) || 'chat', jump: 0 };
const listeners = new Set<() => void>();

function set(next: CurrentPage): void {
  current = next;
  tabStorage()?.setItem(STORAGE_KEY, next.page);
  listeners.forEach((listener) => listener());
}

/** Scroll the pager to `page`. */
export function showPage(page: HomePage): void {
  set({ page, jump: current.jump + 1 });
}

/** The user swiped to `page`; the pager is already there. */
export function pageSwiped(page: HomePage): void {
  if (page !== current.page) set({ page, jump: current.jump });
}

/** Back to a cold launch's Chat page (sign-out). */
export function resetCurrentPage(): void {
  tabStorage()?.removeItem(STORAGE_KEY);
  current = { page: 'chat', jump: current.jump + 1 };
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getCurrentPage(): CurrentPage {
  return current;
}

export function useCurrentPage(): CurrentPage {
  return useSyncExternalStore(subscribe, getCurrentPage, getCurrentPage);
}
