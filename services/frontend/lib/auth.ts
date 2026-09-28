import { Platform } from 'react-native';

import { ApiError, apiFetch } from './api';
import { homeAiClientHeader } from './client';
import { loadPairedDeviceId, savePairedDeviceId } from './devicePairStore';
import { generateDeviceKey, signChallenge } from './deviceKey';
import { confirmDevicePresence } from './localAuth';
import { setSessionToken } from './session';
import { clearStoredToken, loadStoredToken, saveStoredToken } from './tokenStore';

/**
 * Client for the platform's `/api/auth/*` routes (`docs/ARCHITECTURE.md`
 * §3 "Platform API"). Web sessions are the `homeai_session` cookie the
 * platform sets; native sends `X-HomeAI-Client: native` (Expo Go) or
 * `host` (dev client), gets `session_token` back instead, and persists it
 * via `tokenStore` so every later request can send it as a bearer
 * (`lib/session.ts`).
 */

export interface User {
  id: string;
  username: string;
  display_name: string;
  role: 'admin' | 'member';
  totp_enabled: boolean;
  require_passkeys?: boolean;
  disabled_at: string | null;
  created_at: string;
}

export interface WebAuthnStatus {
  rp_id?: string | null;
  origin_ok: boolean;
}

export interface AuthStatus {
  setup_required: boolean;
  authenticated: boolean;
  user?: User;
  webauthn?: WebAuthnStatus;
  public_https?: boolean;
  origin?: 'lan' | 'vpn' | 'public';
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
  const client = homeAiClientHeader();
  if (client) headers['X-HomeAI-Client'] = client;
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

export function passkeysAvailable(status?: WebAuthnStatus | null): boolean {
  return Boolean(status?.rp_id && status.origin_ok);
}

export async function login({ username, password, totpCode }: LoginInput): Promise<User> {
  const body: Record<string, string> = { username, password, device_label: DEVICE_LABEL };
  if (totpCode) body.totp_code = totpCode;
  return adoptSession(await postJson<SessionResponse>('/api/auth/login', body));
}

export async function beginDeviceLogin(deviceId: string): Promise<{ challenge: string; expires_at: string }> {
  return postJson<{ challenge: string; expires_at: string }>('/api/auth/device/begin', {
    device_id: deviceId,
  });
}

export async function finishDeviceLogin(input: {
  deviceId: string;
  signature: string;
  totpCode?: string;
}): Promise<User> {
  const body: Record<string, unknown> = {
    device_id: input.deviceId,
    signature: input.signature,
    device_label: DEVICE_LABEL,
  };
  if (input.totpCode) body.totp_code = input.totpCode;
  return adoptSession(await postJson<SessionResponse>('/api/auth/device/finish', body));
}

export async function enrollDevice(input: {
  token: string;
  publicKey: string;
  name: string;
  signature: string;
}): Promise<{ id: string; name: string }> {
  const device = await postJson<{ id: string; name: string }>('/api/auth/device/enroll', {
    token: input.token,
    public_key: input.publicKey,
    name: input.name,
    signature: input.signature,
  });
  await savePairedDeviceId(device.id);
  return device;
}

/** Host-app login: biometric unlock, sign the challenge, finish. */
export async function loginWithPairedDevice(totpCode?: string): Promise<User> {
  const deviceId = await loadPairedDeviceId();
  if (!deviceId) throw new ApiError(401, 'invalid_credentials');
  if (!(await confirmDevicePresence())) throw new ApiError(401, 'biometric_cancelled');
  const { challenge } = await beginDeviceLogin(deviceId);
  const signature = await signChallenge(challenge);
  return finishDeviceLogin({ deviceId, signature, totpCode });
}

/** Host-app first pair: generate a Keystore key, consume the LAN QR, then
 * sign in with the new pair (the key is still unlocked). */
export async function pairThisDevice(payloadJson: string, name?: string): Promise<User> {
  let payload: { token?: string; challenge?: string; kind?: string };
  try {
    payload = JSON.parse(payloadJson) as { token?: string; challenge?: string; kind?: string };
  } catch {
    throw new ApiError(422, 'invalid_pairing_qr');
  }
  if (payload.kind !== 'homeai-host-pair' || !payload.token || !payload.challenge) {
    throw new ApiError(422, 'invalid_pairing_qr');
  }
  if (!(await confirmDevicePresence())) throw new ApiError(401, 'biometric_cancelled');
  const publicKey = await generateDeviceKey();
  const signature = await signChallenge(payload.challenge);
  const label = name?.trim() || DEVICE_LABEL;
  const device = await enrollDevice({ token: payload.token, publicKey, name: label, signature });
  const { challenge } = await beginDeviceLogin(device.id);
  const loginSig = await signChallenge(challenge);
  return finishDeviceLogin({ deviceId: device.id, signature: loginSig });
}

export { loadPairedDeviceId };

export async function beginPasskeyLogin(username: string): Promise<Record<string, unknown>> {
  return postJson<Record<string, unknown>>('/api/auth/passkey/login/begin', { username });
}

export async function finishPasskeyLogin(input: {
  username: string;
  credential: Record<string, unknown>;
  totpCode?: string;
}): Promise<User> {
  const body: Record<string, unknown> = {
    username: input.username,
    credential: input.credential,
    device_label: DEVICE_LABEL,
  };
  if (input.totpCode) body.totp_code = input.totpCode;
  return adoptSession(await postJson<SessionResponse>('/api/auth/passkey/login/finish', body));
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

/** Re-confirms the password for this session, unlocking admin routes for
 * five minutes (`stepped_up_until`). */
export function stepUp(password: string): Promise<{ stepped_up_until: string }> {
  return postJson<{ stepped_up_until: string }>('/api/auth/step-up', { password });
}

export function beginPasskeyStepUp(): Promise<Record<string, unknown>> {
  return postJson<Record<string, unknown>>('/api/auth/passkey/step-up/begin', {});
}

export function finishPasskeyStepUp(credential: Record<string, unknown>): Promise<{ stepped_up_until: string }> {
  return postJson<{ stepped_up_until: string }>('/api/auth/passkey/step-up/finish', { credential });
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
  passkey_required: 'This account requires a passkey to sign in.',
  domain_required: 'Passkeys need a domain.',
  passkey_rp_mismatch: 'This page does not match the passkey domain.',
  invalid_passkey: "That passkey didn't work. Try again.",
  no_passkey: 'No passkey is registered on this account.',
  passkey_exists: 'That passkey is already registered.',
  invalid_pairing_qr: "That pairing code isn't valid. Scan or paste the QR from Settings on the LAN.",
  biometric_cancelled: 'Unlock cancelled.',
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
