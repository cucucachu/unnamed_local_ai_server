import * as SecureStore from 'expo-secure-store';

/**
 * Native persistence for the platform session token (`hs_…`). Web never
 * holds a token — its session is the `HttpOnly` `homeai_session` cookie —
 * so `tokenStore.web.ts` is a no-op and keeps `expo-secure-store` out of
 * the web bundle.
 */

const KEY = 'homeai_session_token';

export async function loadStoredToken(): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(KEY);
  } catch {
    return null;
  }
}

export async function saveStoredToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(KEY, token);
}

export async function clearStoredToken(): Promise<void> {
  try {
    await SecureStore.deleteItemAsync(KEY);
  } catch {
    // Nothing stored, or the keychain is unavailable — either way there's
    // no token left to use.
  }
}
