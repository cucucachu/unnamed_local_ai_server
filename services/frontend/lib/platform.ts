import { Platform } from 'react-native';

import { ApiError, apiBase, apiFetch } from './api';
import type { User } from './auth';
import { StepUpCancelledError } from './stepUp';

/**
 * Client for the platform's `/api/platform/*` routes (`docs/ARCHITECTURE.md`
 * §3 "Platform API"): the signed-in user's account, sessions, and TOTP;
 * spaces and their members; and the admin user/invite routes. Admin routes
 * (and a stepped-up admin managing another space's members) answer `403
 * step_up_required` until the session re-enters its password — wrap those
 * calls in `useStepUp().withStepUp` (`components/StepUpProvider.tsx`).
 */

export interface Session {
  id: string;
  device_label: string | null;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  current: boolean;
}

export interface TotpEnrollment {
  secret: string;
  otpauth_uri: string;
}

export type SpaceRole = 'owner' | 'editor' | 'viewer';

export interface Space {
  id: string;
  slug: string;
  name: string;
  kind: 'personal' | 'shared';
  gid: number;
  owner_user_id: string | null;
  role: SpaceRole | null;
  created_at: string;
  archived_at: string | null;
}

/** Viewers, and anyone without a role in the space, cannot write. */
export function isReadOnly(space: Space): boolean {
  return space.role !== 'owner' && space.role !== 'editor';
}

export interface Member {
  user_id: string;
  username: string;
  display_name: string;
  role: SpaceRole;
  added_at: string;
}

export interface DirectoryUser {
  id: string;
  username: string;
  display_name: string;
}

export interface Invite {
  id: string;
  label: string | null;
  status: 'pending' | 'used' | 'expired' | 'revoked';
  created_by: string | null;
  created_at: string;
  expires_at: string;
  used_at: string | null;
  used_by: string | null;
  revoked_at: string | null;
}

export interface CreatedInvite extends Invite {
  token: string;
  accept_url: string;
}

function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  return apiFetch<T>(
    path,
    body === undefined
      ? { method }
      : { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) },
  );
}

export function updateMe(changes: {
  display_name?: string;
  password?: string;
  current_password?: string;
}): Promise<User> {
  return send<User>('PATCH', '/api/platform/me', changes);
}

export async function listSessions(): Promise<Session[]> {
  return (await apiFetch<{ sessions: Session[] }>('/api/platform/me/sessions')).sessions;
}

export function revokeSession(id: string): Promise<void> {
  return send<void>('DELETE', `/api/platform/me/sessions/${encodeURIComponent(id)}`);
}

export interface WireGuardDevice {
  id: string;
  name: string;
  address: string;
  created_at: string;
}

export interface CreatedWireGuardDevice extends WireGuardDevice {
  config: string;
}

export async function listWireGuardDevices(): Promise<WireGuardDevice[]> {
  return (await apiFetch<{ devices: WireGuardDevice[] }>('/api/platform/me/wireguard-devices')).devices;
}

export function createWireGuardDevice(name: string): Promise<CreatedWireGuardDevice> {
  return send<CreatedWireGuardDevice>('POST', '/api/platform/me/wireguard-devices', { name });
}

export function revokeWireGuardDevice(id: string): Promise<void> {
  return send<void>('DELETE', `/api/platform/me/wireguard-devices/${encodeURIComponent(id)}`);
}

export function enrollTotp(password: string): Promise<TotpEnrollment> {
  return send<TotpEnrollment>('POST', '/api/platform/me/totp/enroll', { password });
}

export function confirmTotp(code: string): Promise<User> {
  return send<User>('POST', '/api/platform/me/totp/confirm', { code });
}

export function disableTotp(password: string): Promise<User> {
  return send<User>('POST', '/api/platform/me/totp/disable', { password });
}

export async function listDirectory(): Promise<DirectoryUser[]> {
  return (await apiFetch<{ users: DirectoryUser[] }>('/api/platform/users/directory')).users;
}

export async function listSpaces(): Promise<Space[]> {
  return (await apiFetch<{ spaces: Space[] }>('/api/platform/spaces')).spaces;
}

export function createSpace(input: { slug: string; name: string }): Promise<Space> {
  return send<Space>('POST', '/api/platform/spaces', input);
}

export function getSpace(id: string): Promise<Space> {
  return apiFetch<Space>(`/api/platform/spaces/${encodeURIComponent(id)}`);
}

export async function listMembers(spaceId: string): Promise<Member[]> {
  return (await apiFetch<{ members: Member[] }>(`/api/platform/spaces/${encodeURIComponent(spaceId)}/members`))
    .members;
}

export function addMember(spaceId: string, userId: string, role: SpaceRole): Promise<Member> {
  return send<Member>('POST', `/api/platform/spaces/${encodeURIComponent(spaceId)}/members`, {
    user_id: userId,
    role,
  });
}

export function updateMemberRole(spaceId: string, userId: string, role: SpaceRole): Promise<Member> {
  return send<Member>(
    'PATCH',
    `/api/platform/spaces/${encodeURIComponent(spaceId)}/members/${encodeURIComponent(userId)}`,
    { role },
  );
}

export function removeMember(spaceId: string, userId: string): Promise<void> {
  return send<void>(
    'DELETE',
    `/api/platform/spaces/${encodeURIComponent(spaceId)}/members/${encodeURIComponent(userId)}`,
  );
}

export async function adminListUsers(): Promise<User[]> {
  return (await apiFetch<{ users: User[] }>('/api/platform/admin/users')).users;
}

export function adminUpdateUser(id: string, changes: { role?: User['role']; disabled?: boolean }): Promise<User> {
  return send<User>('PATCH', `/api/platform/admin/users/${encodeURIComponent(id)}`, changes);
}

export async function adminListInvites(): Promise<Invite[]> {
  return (await apiFetch<{ invites: Invite[] }>('/api/platform/admin/invites')).invites;
}

export function adminCreateInvite(label?: string): Promise<CreatedInvite> {
  return send<CreatedInvite>('POST', '/api/platform/admin/invites', label ? { label } : {});
}

export function adminRevokeInvite(id: string): Promise<void> {
  return send<void>('DELETE', `/api/platform/admin/invites/${encodeURIComponent(id)}`);
}

/** The link a new member opens to accept `token`. Built from where this app
 * reaches the server (the page's origin on web, `EXPO_PUBLIC_API_HOST` on
 * native) rather than the platform's `accept_url`, whose host is whatever
 * the platform saw on the request. */
export function inviteLink(token: string): string {
  const path = `/invite?token=${encodeURIComponent(token)}`;
  if (Platform.OS === 'web') {
    return window.location.origin + path;
  }
  return apiBase().replace(/\/+$/, '') + path;
}

/** Lowercase, hyphenated slug suggestion for a space `name` (the platform's
 * `^[a-z0-9][a-z0-9-]{0,39}$`). */
export function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40)
    .replace(/-+$/, '');
}

const MESSAGES: Record<string, string> = {
  invalid_password: 'Incorrect password.',
  current_password_required: 'Enter your current password.',
  weak_password: 'Passwords must be at least 8 characters.',
  invalid_display_name: 'Display names must be 1–64 printable characters.',
  invalid_totp: "That code didn't work. Try again.",
  totp_already_enabled: 'Two-factor authentication is already on.',
  totp_not_pending: 'Start setup again.',
  totp_not_enabled: 'Two-factor authentication is already off.',
  invalid_slug: 'Short names are lowercase letters, digits, and "-", starting with a letter or digit (max 40).',
  reserved_slug: 'That short name is reserved. Pick another.',
  invalid_name: 'Names must be 1–64 printable characters.',
  slug_taken: 'That short name is taken.',
  personal_space: "Personal spaces can't be shared.",
  unknown_user: "That user doesn't exist.",
  user_disabled: 'That user is disabled.',
  already_member: 'Already a member.',
  last_owner: 'A space needs at least one owner.',
  last_admin: 'The server needs at least one enabled admin.',
  invalid_label: 'Labels must be at most 64 printable characters.',
  insufficient_role: "You don't have permission to do that.",
  admin_required: 'Only admins can do that.',
  step_up_required: 'Confirm your password to continue.',
  not_found: 'Not found. It may have been removed.',
  unknown_commit: "That version isn't in this app's history.",
  history_unavailable: "App history isn't available on this server right now.",
  history_failed: "Couldn't read this app's history.",
  builder_unavailable: "The app builder isn't available right now. Try again later.",
  not_built: 'Build the app before publishing it.',
  version_exists: 'That version is already published. Bump the version in app.json and build again.',
  not_in_catalog: "This app isn't listed in that space's catalog.",
  permissions_required: 'Confirm the permissions this app is asking for.',
  permissions_changed: 'This update asks for different permissions. Review them and confirm.',
  permissions_mismatch: "The permissions you granted don't match what the app requested.",
  working_not_updatable: 'The working copy updates when you build. Publish a version to update other installs.',
  already_on_version: 'This install is already on that version.',
  not_published: "This app hasn't been published, so it can't be copied without its source.",
  too_many_devices: 'This account already has the maximum number of WireGuard devices.',
  peers_exhausted: 'The VPN has no free addresses left. Revoke an unused device and try again.',
  unknown_device: "That VPN device isn't on this account.",
};

/** Human-readable text for a failed platform call. */
export function platformErrorMessage(error: unknown): string {
  if (error instanceof StepUpCancelledError) return MESSAGES.step_up_required;
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
