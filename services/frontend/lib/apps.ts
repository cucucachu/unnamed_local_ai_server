import { ApiError, apiFetch } from './api';
import { listSpaces, type Space } from './platform';

/**
 * Installed app instances (`GET /api/platform/spaces/{id}/instances`,
 * `docs/ARCHITECTURE.md` §3 "Apps"). The platform lists instances per space,
 * so the Apps tab asks every space the user belongs to.
 */

export interface InstanceApp {
  id: string;
  slug: string;
  name: string;
  version: string | null;
  icon: string | null;
}

export interface Instance {
  id: string;
  app_id: string;
  space_id: string;
  /** `"working"`, or a pinned published version's id. */
  tracks: string;
  installed_by: string | null;
  granted_permissions: Record<string, unknown>;
  created_at: string;
  app: InstanceApp;
  update: { id: string; version: string; permissions: Record<string, unknown> } | null;
}

export interface SpaceApps {
  space: Space;
  instances: Instance[];
}

export async function listInstances(spaceId: string): Promise<Instance[]> {
  return (
    await apiFetch<{ instances: Instance[] }>(`/api/platform/spaces/${encodeURIComponent(spaceId)}/instances`)
  ).instances;
}

/** Every live space's instances, Personal first, then shared spaces by name. */
export async function listInstalledApps(): Promise<SpaceApps[]> {
  const spaces = (await listSpaces())
    .filter((space) => space.archived_at === null)
    .sort((a, b) => (a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === 'personal' ? -1 : 1));
  return Promise.all(spaces.map(async (space) => ({ space, instances: await listInstances(space.id) })));
}

/** `GET /api/platform/apps/{id}`; `source_path`/`working_version` are null
 * when the user sees the app only through an install. */
export interface App {
  id: string;
  slug: string;
  name: string;
  source_space_id: string;
  source_path: string | null;
  working_version: { version: string; commit: string | null } | null;
}

/** One entry of an app's source history: a successful build, or a revert. */
export interface AppCommit {
  id: string;
  parent: string | null;
  kind: 'build' | 'revert';
  subject: string;
  version: string | null;
  user: string | null;
  thread_id: string | null;
  reverts: string | null;
  created_at: string;
  current: boolean;
}

export interface AppHistoryPage {
  commits: AppCommit[];
  next_offset: number | null;
}

export interface RevertResult {
  commit: string;
  ok: boolean;
  diagnostics: { message: string }[];
  migrations: { migration: { status: string } | null; error: string | null }[];
}

export function getApp(appId: string): Promise<App> {
  return apiFetch<App>(`/api/platform/apps/${encodeURIComponent(appId)}`);
}

export function getAppHistory(appId: string, offset = 0): Promise<AppHistoryPage> {
  return apiFetch<AppHistoryPage>(`/api/platform/apps/${encodeURIComponent(appId)}/history?offset=${offset}`);
}

/** Restores the source as of `commit` (a new commit) and rebuilds; `ok` is the rebuild's. */
export function revertApp(appId: string, commit: string): Promise<RevertResult> {
  return apiFetch<RevertResult>(`/api/platform/apps/${encodeURIComponent(appId)}/revert`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ commit }),
  });
}

/** The instance and the space it's installed in (for the user's role there);
 * `ApiError 404 not_found` when the user can't see it. */
export async function findInstance(instanceId: string): Promise<{ space: Space; instance: Instance }> {
  for (const { space, instances } of await listInstalledApps()) {
    const instance = instances.find((i) => i.id === instanceId);
    if (instance) return { space, instance };
  }
  throw new ApiError(404, 'not_found');
}

export interface AppVersion {
  id: string;
  version: string;
  kind: 'working' | 'published';
  commit: string | null;
  manifest: {
    name?: string;
    homeai?: { description?: string; icon?: string; permissions?: Record<string, unknown> };
  };
  bundle_path: string | null;
  created_at: string;
  published_at: string | null;
}

export interface CatalogEntry {
  app: InstanceApp;
  version: AppVersion;
  installed: boolean;
  instance_id: string | null;
}

export function listCatalog(spaceId: string): Promise<{ entries: CatalogEntry[] }> {
  return apiFetch<{ entries: CatalogEntry[] }>(`/api/platform/spaces/${encodeURIComponent(spaceId)}/catalog`);
}

export function publishApp(appId: string, spaceIds: string[]): Promise<{ version: AppVersion; space_ids: string[] }> {
  return apiFetch(`/api/platform/apps/${encodeURIComponent(appId)}/publish`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ space_ids: spaceIds }),
  });
}

export function installApp(
  spaceId: string,
  appId: string,
  tracks: string,
  grantedPermissions?: Record<string, unknown>,
): Promise<Instance> {
  return apiFetch(`/api/platform/spaces/${encodeURIComponent(spaceId)}/instances`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      app_id: appId,
      tracks,
      ...(grantedPermissions === undefined ? {} : { granted_permissions: grantedPermissions }),
    }),
  });
}

export function updateInstance(
  spaceId: string,
  instanceId: string,
  versionId: string,
  grantedPermissions?: Record<string, unknown>,
): Promise<{ instance: Instance; migration: { status: string } }> {
  return apiFetch(
    `/api/platform/spaces/${encodeURIComponent(spaceId)}/instances/${encodeURIComponent(instanceId)}/update`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        version_id: versionId,
        ...(grantedPermissions === undefined ? {} : { granted_permissions: grantedPermissions }),
      }),
    },
  );
}

export function forkApp(appId: string, spaceId: string, slug?: string): Promise<{ app: App; instance: Instance }> {
  return apiFetch(`/api/platform/apps/${encodeURIComponent(appId)}/fork`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ space_id: spaceId, ...(slug ? { slug } : {}) }),
  });
}

export function permissionsOf(manifest: AppVersion['manifest'] | undefined): Record<string, unknown> {
  const perms = manifest?.homeai?.permissions;
  return perms && typeof perms === 'object' ? perms : {};
}

export function describePermissions(perms: Record<string, unknown>): string[] {
  return Object.entries(perms).map(([key, value]) =>
    Array.isArray(value) ? `${key}: ${value.join(', ')}` : `${key}: ${JSON.stringify(value)}`,
  );
}
