/**
 * In-memory session state shared by every request path (`apiFetch`, the
 * chat WebSocket, native uploads/downloads/media). Native keeps its bearer
 * token here after `restoreSession()`/login load it from `tokenStore`; web
 * never sets one (the cookie rides along on its own).
 */

let token: string | null = null;
const unauthorizedListeners = new Set<() => void>();

export function sessionToken(): string | null {
  return token;
}

export function setSessionToken(value: string | null): void {
  token = value;
}

/** `Authorization` header for the current native session, or `{}`. */
export function authHeaders(): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Subscribe to "the server rejected our session". Returns an unsubscribe. */
export function onUnauthorized(listener: () => void): () => void {
  unauthorizedListeners.add(listener);
  return () => {
    unauthorizedListeners.delete(listener);
  };
}

export function notifyUnauthorized(): void {
  for (const listener of [...unauthorizedListeners]) listener();
}
