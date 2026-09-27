/** Web half of `tokenStore.ts`: the session is the `HttpOnly` cookie, which
 * JS can neither read nor needs to store. */

export async function loadStoredToken(): Promise<string | null> {
  return null;
}

export async function saveStoredToken(_token: string): Promise<void> {}

export async function clearStoredToken(): Promise<void> {}
