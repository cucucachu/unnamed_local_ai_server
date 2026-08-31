import { Platform } from 'react-native';

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
 * FastAPI error shape (`HTTPException` -> `{"detail": "..."}`). */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

function detailFromBody(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && typeof (body as { detail?: unknown }).detail === 'string') {
    return (body as { detail: string }).detail;
  }
  return fallback;
}

/** JSON fetch wrapper. Resolves with the parsed body typed as `T` on a 2xx
 * response; throws `ApiError` (status + detail) on non-2xx. */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiBase() + path, init);

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }

  if (!response.ok) {
    throw new ApiError(response.status, detailFromBody(body, response.statusText || 'Request failed'));
  }

  return body as T;
}
