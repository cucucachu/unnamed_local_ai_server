import { Platform } from 'react-native';

import { ApiError, apiFetch } from './api';
import { setSessionToken } from './session';
import { clearStoredToken, loadStoredToken, saveStoredToken } from './tokenStore';

/**
 * Client for the platform's `/api/auth/*` routes (`docs/ARCHITECTURE.md`
 * §3 "Platform API"). Web sessions are the `homeai_session` cookie the
 * platform sets; native sends `X-HomeAI-Client: native`, gets
 * `session_token` back instead, and persists it via `tokenStore` so every
 * later request can send it as a bearer (`lib/session.ts`).
 */

export interface User {
  id: string;
  username: string;
  display_name: string;
  role: 'admin' | 'member';
  totp_enabled: boolean;
  disabled_at: string | null;
  created_at: string;
}

export interface AuthStatus {
  setup_required: boolean;
  authenticated: boolean;
  user?: User;
}

interface SessionResponse {
  user: User;
  session_token?: string;
}

export interface LoginInput {
  username: string;
  password: string;
  totpCode?: string;
}

export interface SetupInput {
  setupCode: string;
  username: string;
  displayName: string;
  password: string;
}

export interface InviteAcceptInput {
  token: string;
  username: string;
  displayName: string;
  password: string;
}

const DEVICE_LABEL = Platform.OS === 'web' ? 'Web browser' : `HomeAI app (${Platform.OS})`;

function authFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string> | undefined) };
  if (Platform.OS !== 'web') headers['X-HomeAI-Client'] = 'native';
  return apiFetch<T>(path, { ...init, headers }, { signOutOnUnauthorized: false });
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return authFetch<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

async function adoptSession(response: SessionResponse): Promise<User> {
  if (response.session_token) {
    setSessionToken(response.session_token);
    await saveStoredToken(response.session_token);
  }
  return response.user;
}

/** Loads a persisted native token into memory. No-op on web. */
export async function restoreSession(): Promise<void> {
  setSessionToken(await loadStoredToken());
}

/** Forgets the local credential (the server side is `logout()`'s job). */
export async function clearSession(): Promise<void> {
  setSessionToken(null);
  await clearStoredToken();
}

export function getAuthStatus(): Promise<AuthStatus> {
  return authFetch<AuthStatus>('/api/auth/status');
}

export async function login({ username, password, totpCode }: LoginInput): Promise<User> {
  const body: Record<string, string> = { username, password, device_label: DEVICE_LABEL };
  if (totpCode) body.totp_code = totpCode;
  return adoptSession(await postJson<SessionResponse>('/api/auth/login', body));
}

export async function setup({ setupCode, username, displayName, password }: SetupInput): Promise<User> {
  return adoptSession(
    await postJson<SessionResponse>('/api/auth/setup', {
      setup_code: setupCode,
      username,
      display_name: displayName,
      password,
      device_label: DEVICE_LABEL,
    }),
  );
}

export async function acceptInvite({
  token,
  username,
  displayName,
  password,
}: InviteAcceptInput): Promise<User> {
  return adoptSession(
    await postJson<SessionResponse>('/api/auth/invite/accept', {
      token,
      username,
      display_name: displayName,
      password,
      device_label: DEVICE_LABEL,
    }),
  );
}

/** Revokes the session server-side (best effort) and always clears it
 * locally — a dead network shouldn't strand the user in a session they
 * asked to leave. */
export async function logout(): Promise<void> {
  try {
    await authFetch<void>('/api/auth/logout', { method: 'POST' });
  } catch {
    // Local sign-out still happens below.
  }
  await clearSession();
}

const MESSAGES: Record<string, string> = {
  invalid_credentials: 'Incorrect username or password.',
  totp_required: 'Enter the 6-digit code from your authenticator app.',
  invalid_totp: "That code didn't work. Try again.",
  account_disabled: 'This account has been disabled.',
  invalid_setup_code: "That setup code isn't right. Check the platform logs for the current code.",
  setup_complete: 'Setup is already complete. Sign in instead.',
  username_taken: 'That username is taken.',
  invalid_username:
    'Usernames are 1–32 characters: lowercase letters, digits, ".", "_" or "-", starting with a letter or digit.',
  weak_password: 'Passwords must be at least 8 characters.',
  invalid_display_name: 'Display names must be 1–64 printable characters.',
  invalid_invite: 'This invite link is invalid, used, expired, or revoked. Ask for a new one.',
  invalid_request: 'Please fill in every field.',
};

/** Human-readable text for an auth failure (a platform error code, a
 * network failure, or anything else). */
export function authErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.detail === 'rate_limited') {
      return error.retryAfterSeconds
        ? `Too many attempts. Try again in ${error.retryAfterSeconds} seconds.`
        : 'Too many attempts. Wait a minute and try again.';
    }
    return MESSAGES[error.detail] ?? `Something went wrong (${error.detail}).`;
  }
  return "Couldn't reach the server. Check your connection and try again.";
}
