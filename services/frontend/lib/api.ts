import { Platform } from 'react-native';

import { authHeaders, notifyUnauthorized } from './session';

/** Base URL to prefix onto API paths. Web build is same-origin (Caddy serves
 * both the SPA and proxies `/api/*` + `/ws/*`); native builds need a real
 * host since there's no "origin" to be relative to. */
export function apiBase(): string {
  if (Platform.OS === 'web') return '';
  return process.env.EXPO_PUBLIC_API_HOST ?? 'http://homeai.local';
}

/** Build a `ws://`/`wss://` URL for `path` (e.g. `/ws/chat/{id}`). */
export function wsUrl(path: string): string {
  if (Platform.OS === 'web') {
    const { protocol, host } = window.location;
    return `${protocol === 'https:' ? 'wss:' : 'ws:'}//${host}${path}`;
  }
  return apiBase().replace(/^http/, 'ws') + path;
}

/** Thrown by `apiFetch` for any non-2xx response. Mirrors the agent-server's
 * FastAPI error shape (`HTTPException` -> `{"detail": "..."}`), which the
 * platform shares (its `detail` is a stable lower-snake code). */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  /** From a `429`'s `Retry-After` header, when present. */
  readonly retryAfterSeconds: number | null;

  constructor(status: number, detail: string, retryAfterSeconds: number | null = null) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

/** Exported for `lib/files.ts`'s `uploadOneNative`, which parses an
 * `expo-file-system` `File.upload()` response body itself (that API
 * doesn't go through `apiFetch`, but its error body shape is identical). */
export function detailFromBody(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && typeof (body as { detail?: unknown }).detail === 'string') {
    return (body as { detail: string }).detail;
  }
  return fallback;
}

function retryAfterFrom(response: Response): number | null {
  const value = Number(response.headers?.get?.('Retry-After'));
  return Number.isFinite(value) && value > 0 ? value : null;
}

export interface ApiFetchOptions {
  /** Default `true`: a `401` means our session is gone, so every
   * `onUnauthorized` listener (the `AuthProvider`) is told to drop back to
   * Login. The `/api/auth/*` calls opt out — their `401`s are "wrong
   * password"-style answers, not a lost session. */
  signOutOnUnauthorized?: boolean;
}

/** JSON fetch wrapper and the one place app requests pick up credentials:
 * same-origin cookie on web, `Authorization: Bearer` on native. Resolves
 * with the parsed body typed as `T` on a 2xx response; throws `ApiError`
 * (status + detail) on non-2xx. */
export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
  { signOutOnUnauthorized = true }: ApiFetchOptions = {},
): Promise<T> {
  const response = await fetch(apiBase() + path, {
    ...init,
    credentials: 'include',
    headers: { ...authHeaders(), ...(init?.headers as Record<string, string> | undefined) },
  });

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }

  if (!response.ok) {
    if (response.status === 401 && signOutOnUnauthorized) notifyUnauthorized();
    throw new ApiError(
      response.status,
      detailFromBody(body, response.statusText || 'Request failed'),
      retryAfterFrom(response),
    );
  }

  return body as T;
}

/** Asks the platform whether our session still holds, for failures that
 * carry no HTTP status of their own (a WebSocket upgrade rejected by
 * `forward_auth` surfaces only as a close). Signs out only on a definite
 * `authenticated: false`; an unreachable platform proves nothing. */
export async function probeSession(): Promise<void> {
  try {
    const status = await apiFetch<{ authenticated?: boolean }>('/api/auth/status', undefined, {
      signOutOnUnauthorized: false,
    });
    if (status?.authenticated === false) notifyUnauthorized();
  } catch {
    // Unreachable or malformed — leave the session alone.
  }
}
