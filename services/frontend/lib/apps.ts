import { ApiError, apiFetch } from './api';
import { listSpaces, type Space } from './platform';

/**
 * Installed app instances (`GET /api/platform/spaces/{id}/instances`,
 * `docs/ARCHITECTURE.md` §3 "Apps"). The platform lists instances per space,
 * so the Home launcher asks every space the user belongs to.
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
  /** The user's arrangement of the space's page (#465), see `arrangeTiles`. */
  order: string[];
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
  return Promise.all(
    spaces.map(async (space) => {
      const [instances, order] = await Promise.all([listInstances(space.id), getAppOrder(space.id).catch(() => [])]);
      return { space, instances, order };
    }),
  );
}

function appOrderPath(spaceId: string): string {
  return `/api/platform/spaces/${encodeURIComponent(spaceId)}/app-order`;
}

/** The user's order of a space's home-page tiles: instance ids and built-in tile names. */
export async function getAppOrder(spaceId: string): Promise<string[]> {
  return (await apiFetch<{ items: string[] }>(appOrderPath(spaceId))).items;
}

export async function setAppOrder(spaceId: string, items: string[]): Promise<string[]> {
  return (
    await apiFetch<{ items: string[] }>(appOrderPath(spaceId), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items }),
    })
  ).items;
}

/** `keys` (the default order) arranged by `saved`: the saved ones that still exist, in
 * their order, then the rest (a new install goes last). */
export function arrangeTiles(keys: string[], saved: string[]): string[] {
  const present = new Set(keys);
  const placed = saved.filter((key) => present.has(key));
  const placedSet = new Set(placed);
  return [...placed, ...keys.filter((key) => !placedSet.has(key))];
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

export interface BuildResult {
  ok: boolean;
  diagnostics: { message: string }[];
}

/** Rebuilds the working version (`POST /apps/{id}/build`); `ok` is the build's. */
export function buildApp(appId: string): Promise<BuildResult> {
  return apiFetch<BuildResult>(`/api/platform/apps/${encodeURIComponent(appId)}/build`, { method: 'POST' });
}

/** Removes the instance and its data from the space. */
export async function uninstallInstance(spaceId: string, instanceId: string): Promise<void> {
  await apiFetch<void>(
    `/api/platform/spaces/${encodeURIComponent(spaceId)}/instances/${encodeURIComponent(instanceId)}`,
    { method: 'DELETE' },
  );
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

/** A starter app the platform ships (`GET /api/platform/app-templates`, M22-05). */
export interface AppTemplate {
  slug: string;
  name: string;
  version: string;
  icon: string | null;
  description: string;
}

export async function listAppTemplates(): Promise<AppTemplate[]> {
  return (await apiFetch<{ templates: AppTemplate[] }>('/api/platform/app-templates')).templates;
}

/** Copies `template` into `spaceId` as a new app, installs it and builds it. */
export function installTemplate(
  spaceId: string,
  template: string,
  slug: string,
  name: string,
): Promise<{ app: App; instance: Instance; build: BuildResult | null }> {
  return apiFetch(
    `/api/platform/spaces/${encodeURIComponent(spaceId)}/app-templates/${encodeURIComponent(template)}/install`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slug, name }),
    },
  );
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

/** One table of an instance in `GET /api/platform/data-map` (M22-06). */
export interface DataMapTable {
  name: string;
  rows: number | null;
  description: string | null;
  columns: Record<string, string>;
}

export interface DataMapInstance {
  id: string;
  app: { id: string; slug: string; name: string };
  space: { id: string; slug: string; name: string; kind: string; role: string };
  description: string | null;
  tables: DataMapTable[];
}

export async function getDataMap(): Promise<DataMapInstance[]> {
  return (await apiFetch<{ instances: DataMapInstance[] }>('/api/platform/data-map')).instances;
}

export interface ExportResult {
  /** The new folder's virtual path, e.g. `/spaces/family/Exports/notes-2026-10-18-120000`. */
  folder: string;
  tables: { name: string; file: string; rows: number }[];
}

/** Every table as `<table>.csv` plus `schema.sql`, in a new folder (M22-08). */
export function exportAppData(instanceId: string, folder?: string): Promise<ExportResult> {
  return apiFetch(`/api/platform/apps/instances/${encodeURIComponent(instanceId)}/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(folder ? { folder } : {}),
  });
}

export type CsvDelimiter = ',' | ';' | '\t';

export interface ImportResult {
  table: string;
  rows: number;
  /** CSV header -> table column, for the headers imported. */
  mapping: Record<string, string>;
  skipped: string[];
  dry_run: boolean;
  headers: string[];
  columns: string[];
  /** Headers with no column of that name; set only by a dry run without a mapping. */
  unmatched: string[];
}

/** Imports one CSV file from Files into `table`, all rows or none; `dryRun` checks it
 * and rolls back. Fails with `ApiError 422 import_failed` (see `importErrorMessage`). */
export function importCsv(
  instanceId: string,
  body: { table: string; path: string; mapping?: Record<string, string>; delimiter?: CsvDelimiter; dryRun?: boolean },
): Promise<ImportResult> {
  return apiFetch(`/api/platform/apps/instances/${encodeURIComponent(instanceId)}/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      table: body.table,
      path: body.path,
      mapping: body.mapping ?? null,
      delimiter: body.delimiter ?? ',',
      dry_run: body.dryRun ?? false,
    }),
  });
}

/** An `import_failed` error's line, column and reason, or null for any other error. */
export function importErrorMessage(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.detail !== 'import_failed') return null;
  const body = (error.body ?? {}) as { message?: string; line?: number | null; column?: string | null };
  const where = [body.line ? `Line ${body.line}` : null, body.column ? `column ${body.column}` : null].filter(Boolean);
  return `Nothing was imported. ${where.length ? `${where.join(', ')}: ` : ''}${body.message ?? ''}`.trim();
}
