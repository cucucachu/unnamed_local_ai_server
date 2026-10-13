import { apiFetch } from './api';
import type { ToolPermission } from './platform';

/**
 * Client for integrations (MCP servers, docs/INTEGRATIONS.md): the
 * signed-in user's (`/api/platform/integrations`) and the admin's
 * (`/api/platform/admin/integrations`, stepped up — wrap in
 * `useStepUp().withStepUp`). No response ever carries a credential.
 */

export type CredentialModel = 'per_user' | 'shared' | 'none';
export type AuthMethod = 'none' | 'token' | 'oauth';
export type ConnectionStatus = 'ready' | 'connected' | 'not_connected' | 'needs_reconnecting';

export interface IntegrationTool {
  name: string;
  title: string | null;
  description: string;
  annotations: Record<string, unknown>;
  enabled: boolean;
  permission: ToolPermission;
  enabled_locked: boolean;
  permission_locked: boolean;
}

export interface Integration {
  id: string;
  slug: string;
  name: string;
  description: string;
  credential_model: CredentialModel;
  auth_method: AuthMethod;
  /** A credential for it would cross the network unencrypted. */
  plain_http: boolean;
  status: ConnectionStatus;
  tools: IntegrationTool[];
}

export interface AdminIntegrationTool {
  name: string;
  title: string | null;
  description: string;
  annotations: Record<string, unknown>;
  enabled: boolean;
  /** `ask`: everyone is asked first, whatever they set. */
  ceiling: ToolPermission;
}

export interface IntegrationConnection {
  user_id: string;
  username: string | null;
  display_name: string | null;
  status: 'connected' | 'needs_reconnecting';
  updated_at: string;
}

export interface AdminIntegration {
  id: string;
  slug: string;
  name: string;
  description: string;
  credential_model: CredentialModel;
  auth_method: AuthMethod;
  url: string;
  plain_http: boolean;
  cert_fingerprint: string | null;
  private_address: boolean;
  token_header: string | null;
  server_info: Record<string, unknown> | null;
  protocol_version: string | null;
  discovered_at: string | null;
  installed_by: string | null;
  created_at: string;
  /** Only for a shared integration: is the household's credential set. */
  shared_credential: ConnectionStatus | null;
  connections: IntegrationConnection[];
  tools: AdminIntegrationTool[];
  resources: { uri: string; name: string; title: string | null; description: string }[];
  prompts: { name: string; title: string | null; description: string }[];
}

/** The gateway's look at a server, without a credential. */
export interface ServerCheck {
  reachable: boolean;
  auth: AuthMethod | null;
  problem: string | null;
  detail: string | null;
  /** SHA-256 of a certificate the proxy refused, to confirm. */
  certificate: string | null;
  protocol_version: string | null;
  server: Record<string, unknown> | null;
}

/** A credential was stored; `checked` false means the server couldn't be asked. */
export interface Connection {
  status: ConnectionStatus;
  checked: boolean;
  problem: string | null;
}

export interface InstallFields {
  slug: string;
  name: string;
  url: string;
  description?: string;
  credential_model: CredentialModel;
  auth_method: AuthMethod;
  cert_fingerprint?: string | null;
  private_address?: boolean;
  token_header?: string | null;
}

function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  return apiFetch<T>(
    path,
    body === undefined
      ? { method }
      : { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) },
  );
}

const MINE = '/api/platform/integrations';
const ADMIN = '/api/platform/admin/integrations';
const id = encodeURIComponent;

export async function listMyIntegrations(): Promise<Integration[]> {
  return (await apiFetch<{ integrations: Integration[] }>(MINE)).integrations;
}

/** Change the user's own settings for an integration's tools; returns the new list. */
export async function changeMyIntegrationTools(
  integrationId: string,
  changes: { name: string; enabled?: boolean; permission?: ToolPermission }[],
): Promise<Integration[]> {
  return (await send<{ integrations: Integration[] }>('PATCH', `${MINE}/${id(integrationId)}/tools`, { changes }))
    .integrations;
}

export function connectIntegration(integrationId: string, secret: string): Promise<Connection> {
  return send<Connection>('PUT', `${MINE}/${id(integrationId)}/connection`, { secret });
}

export function disconnectIntegration(integrationId: string): Promise<void> {
  return send<void>('DELETE', `${MINE}/${id(integrationId)}/connection`);
}

export async function adminListIntegrations(): Promise<AdminIntegration[]> {
  return (await apiFetch<{ integrations: AdminIntegration[] }>(ADMIN)).integrations;
}

export function adminInstallIntegration(fields: InstallFields): Promise<AdminIntegration> {
  return send<AdminIntegration>('POST', ADMIN, fields);
}

export function adminEditIntegration(
  integrationId: string,
  changes: Partial<InstallFields>,
): Promise<AdminIntegration> {
  return send<AdminIntegration>('PATCH', `${ADMIN}/${id(integrationId)}`, changes);
}

export function adminUninstallIntegration(integrationId: string): Promise<void> {
  return send<void>('DELETE', `${ADMIN}/${id(integrationId)}`);
}

export function adminCheckIntegration(integrationId: string): Promise<ServerCheck> {
  return send<ServerCheck>('POST', `${ADMIN}/${id(integrationId)}/check`);
}

export function adminRefreshIntegration(integrationId: string): Promise<AdminIntegration> {
  return send<AdminIntegration>('POST', `${ADMIN}/${id(integrationId)}/refresh`);
}

export function adminChangeIntegrationTools(
  integrationId: string,
  changes: { name: string; enabled?: boolean; ceiling?: ToolPermission }[],
): Promise<AdminIntegration> {
  return send<AdminIntegration>('PATCH', `${ADMIN}/${id(integrationId)}/tools`, { changes });
}

export function adminSetSharedCredential(integrationId: string, secret: string): Promise<Connection> {
  return send<Connection>('PUT', `${ADMIN}/${id(integrationId)}/shared-credential`, { secret });
}

export function adminClearSharedCredential(integrationId: string): Promise<void> {
  return send<void>('DELETE', `${ADMIN}/${id(integrationId)}/shared-credential`);
}

export function adminDisconnectUser(integrationId: string, userId: string): Promise<void> {
  return send<void>('DELETE', `${ADMIN}/${id(integrationId)}/connections/${id(userId)}`);
}

/** What the install rules (docs/INTEGRATIONS.md §4) make of a URL, before asking the server.
 * The platform decides; this is for showing the right warning as the admin types. */
export interface AddressFacts {
  /** Why it can't be used at all, as a platform error code. */
  refused: 'invalid_url' | 'loopback_refused' | 'mdns_name' | 'https_required' | null;
  scheme: 'http' | 'https' | null;
  host: string;
  port: number;
  /** `private`: an IP on a home network (needs the admin's exception);
   * `name`: a DNS name, which may point either way. */
  kind: 'private' | 'public' | 'name';
}

/** null: not an IP literal; 'never': loopback and the like. */
function ipKind(host: string): 'private' | 'public' | 'never' | null {
  if (/^\d+\.\d+\.\d+\.\d+$/.test(host)) {
    const parts = host.split('.').map(Number);
    if (parts.some((p) => p > 255)) return 'never';
    const [a, b] = parts;
    if (a === 127 || a === 0 || a >= 224) return 'never';
    const home = a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 169 && b === 254);
    return home ? 'private' : 'public';
  }
  if (host.includes(':')) {
    if (host === '::1' || host === '::' || host.startsWith('ff')) return 'never';
    return /^(f[cd]|fe[89ab])/.test(host) ? 'private' : 'public';
  }
  return null;
}

export function addressFacts(url: string): AddressFacts {
  const none: AddressFacts = { refused: 'invalid_url', scheme: null, host: '', port: 0, kind: 'name' };
  const match = /^(https?):\/\/([^/?#]+)/i.exec(url.trim());
  if (!match) return none;
  const scheme = match[1].toLowerCase() as 'http' | 'https';
  const authority = match[2];
  if (authority.includes('@')) return { ...none, scheme };
  const hostPort = /^(\[[^\]]+\]|[^:]+)(?::(\d{1,5}))?$/.exec(authority);
  if (!hostPort) return { ...none, scheme };
  const host = hostPort[1].toLowerCase().replace(/^\[|\]$/g, '').replace(/\.$/, '');
  const port = hostPort[2] ? Number(hostPort[2]) : scheme === 'https' ? 443 : 80;
  if (!host || port < 1 || port > 65535) return { ...none, scheme };
  const ip = ipKind(host);
  const facts: AddressFacts = { refused: null, scheme, host, port, kind: ip === 'private' || ip === 'public' ? ip : 'name' };
  if (ip === 'never' || host === 'localhost' || host.endsWith('.localhost')) {
    return { ...facts, refused: 'loopback_refused' };
  }
  if (host.endsWith('.local')) return { ...facts, refused: 'mdns_name' };
  if (scheme === 'http' && facts.kind === 'public') return { ...facts, refused: 'https_required' };
  return facts;
}

/** Whether this address can only be used with the admin's private-address exception. */
export function needsException(facts: AddressFacts): boolean {
  return facts.refused === null && (facts.kind === 'private' || (facts.kind === 'name' && facts.scheme === 'http'));
}

/** `ab12…` as `AB:12:…`, the way certificate viewers show it. */
export function formatFingerprint(hex: string): string {
  return (hex.toUpperCase().match(/.{2}/g) ?? []).join(':');
}

export function statusLabel(integration: Pick<Integration, 'status' | 'credential_model'>): string {
  switch (integration.status) {
    case 'ready':
      return 'Ready';
    case 'connected':
      return integration.credential_model === 'shared' ? 'Shared' : 'Connected';
    case 'needs_reconnecting':
      return 'Needs reconnecting';
    default:
      return integration.credential_model === 'shared' ? 'Not set up' : 'Not connected';
  }
}

/** The server's own hints about a tool, shown as information only. */
export function annotationLabels(annotations: Record<string, unknown>): string[] {
  const labels: string[] = [];
  if (annotations.readOnlyHint === true) labels.push('Says: read-only');
  if (annotations.destructiveHint === true) labels.push('Says: can delete or overwrite');
  if (annotations.openWorldHint === true) labels.push('Says: reaches outside');
  return labels;
}

export function toolTitle(tool: { name: string; title: string | null }): string {
  return tool.title || tool.name;
}
