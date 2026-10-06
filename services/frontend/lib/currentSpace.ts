import { Platform } from 'react-native';

/**
 * The space page the Apps screen last showed (M19-05), for this app session:
 * a cold launch opens Personal. On web the session is the browser tab
 * (`sessionStorage`), like `currentChat.ts`.
 */
const STORAGE_KEY = 'homeai.currentSpace';

function tabStorage(): Storage | null {
  if (Platform.OS !== 'web') return null;
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
}

let current: string | null = tabStorage()?.getItem(STORAGE_KEY) || null;

export function getCurrentSpace(): string | null {
  return current;
}

export function setCurrentSpace(spaceId: string | null): void {
  current = spaceId;
  const storage = tabStorage();
  if (spaceId === null) storage?.removeItem(STORAGE_KEY);
  else storage?.setItem(STORAGE_KEY, spaceId);
}
