import { Platform } from 'react-native';

import { Directory, File, Paths } from 'expo-file-system';
import * as DocumentPicker from 'expo-document-picker';
import * as Sharing from 'expo-sharing';

import { ApiError, apiBase, apiFetch } from './api';

/**
 * Typed client for the "Reference: Shared Conventions & Contracts" issue
 * (#34), §5 "Files" REST endpoints — implemented server-side in M3-03
 * (`services/agent-server/app/api/files.py`). Do not deviate from these
 * shapes; mirrors that module's Pydantic DTOs (`FileEntryOut`, `FileListOut`,
 * `UploadOut`) field-for-field, and follows `threads.ts`'s established
 * client-module style (docstring citing the exact contract section,
 * `encodeURIComponent` on the query-param `path`, reusing `apiFetch` for
 * every JSON call).
 */

export interface FileEntry {
  name: string;
  path: string;
  type: 'file' | 'dir';
  size: number;
  mtime: string;
  mime: string | null;
}

export interface FileListing {
  path: string;
  entries: FileEntry[];
}

export interface UploadResult {
  uploaded: string[];
}

/** Joins a workspace-relative dir (`""` = root) with a bare file/dir name
 * into one workspace-relative path — the inverse of the server's own
 * `_rel_posix` (see `files.py`). Used to build `dst` for rename/move/copy
 * and the target of a new file/dir. */
export function joinPath(dir: string, name: string): string {
  return dir ? `${dir}/${name}` : name;
}

/** The workspace-relative parent of `path` (`""` for a root-level entry). */
export function parentPath(path: string): string {
  const lastSlash = path.lastIndexOf('/');
  return lastSlash === -1 ? '' : path.slice(0, lastSlash);
}

/** `GET /api/files?path=<dir>` — `entries` are pre-sorted dirs-first,
 * case-insensitive by name, by the server (§5's `list_files`); never
 * re-sorted here per the ticket. `path` is a query PARAM value (not a URL
 * path segment), so `encodeURIComponent` percent-encoding its internal `/`
 * characters as `%2F` is correct, not a bug — the server decodes the whole
 * query value back to the original string regardless of how its `/`s were
 * encoded. */
export async function listFiles(path: string): Promise<FileListing> {
  return apiFetch<FileListing>(`/api/files?path=${encodeURIComponent(path)}`);
}

/** `POST /api/files/mkdir` — `201 {"path": str}`. `409` if a FILE (not a
 * dir) already exists at `path` (server-side `exist_ok=True` only
 * suppresses the "already exists" error for an existing directory). */
export async function mkdir(path: string): Promise<{ path: string }> {
  return apiFetch<{ path: string }>('/api/files/mkdir', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  });
}

/** `POST /api/files/move` — rename == move with the same parent (per the
 * ticket). `409` if `dst` already exists. */
export async function movePath(src: string, dst: string): Promise<{ src: string; dst: string }> {
  return apiFetch<{ src: string; dst: string }>('/api/files/move', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ src, dst }),
  });
}

/** `POST /api/files/copy` — `409` if `dst` already exists. */
export async function copyPath(src: string, dst: string): Promise<{ src: string; dst: string }> {
  return apiFetch<{ src: string; dst: string }>('/api/files/copy', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ src, dst }),
  });
}

/** `DELETE /api/files?path=<p>` — `204 No Content`; see `threads.ts`'s
 * `deleteThread` for why `apiFetch<void>` needs no special-casing for the
 * empty body (its own `try/catch` around `response.json()` already treats
 * an unparsable/empty body as `undefined`). */
export async function deletePath(path: string): Promise<void> {
  await apiFetch<void>(`/api/files?path=${encodeURIComponent(path)}`, { method: 'DELETE' });
}

/** One `FormData` part per uploaded file. RN's own `FormData` polyfill
 * (read directly from `node_modules/react-native/Libraries/Network/
 * FormData.js` for this ticket) treats a plain `{ uri, name, type }` object
 * as its "blob" shape for a file part — the standard idiom used throughout
 * the RN ecosystem for multipart uploads of a `file://` URI. Web instead
 * appends a real `File`/`Blob` (from `<input type="file">`), which every
 * `FormData` implementation (RN's own, and the browser/DOM one this
 * project's `tsconfig` types against) accepts natively. */
export type UploadPart = File | Blob | { uri: string; name: string; type?: string };

/**
 * Platform-independent core of the upload flow — `path` (target dir) +
 * repeated `file` fields per §5, POSTed as `multipart/form-data`. Exported
 * separately from `pickAndUpload` below so it's unit-testable without a
 * real file picker (see `lib/__tests__/files.test.ts`).
 *
 * Deviation from the ticket's framing ("upload/download need raw fetch/
 * native APIs instead since they're not plain JSON... even where you can't
 * reuse `apiFetch` itself directly"): upload actually CAN reuse `apiFetch`
 * directly, confirmed by reading `apiFetch`'s own body (`lib/api.ts`) — it
 * never inspects or sets a request `Content-Type`, it just hands `init`
 * straight to `fetch`, and it only cares that the *response* is JSON (true
 * here: `201 {"uploaded": [...]}`, and error bodies are `{"detail": ...}`
 * exactly like every other endpoint). Handing it a `FormData` body works
 * identically to a bespoke `fetch` call — the runtime sets the multipart
 * boundary header itself whenever the body is `FormData`, on both web and
 * RN's own networking stack. Only `download` genuinely can't reuse
 * `apiFetch` (its response is binary, and `apiFetch` unconditionally calls
 * `response.json()`).
 */
export async function uploadToDir(targetDir: string, parts: UploadPart[]): Promise<UploadResult> {
  const formData = new FormData();
  formData.append('path', targetDir);
  for (const part of parts) {
    // RN's `{ uri, name, type }` blob shape isn't part of the DOM
    // `FormData`/`Blob` types this project's tsconfig checks against.
    formData.append('file', part as unknown as Blob);
  }
  return apiFetch<UploadResult>('/api/files/upload', { method: 'POST', body: formData });
}

/**
 * Bridges native file-picking and web file-picking into one call so the
 * files screen doesn't need its own `Platform.OS` branch. Native uses
 * `expo-document-picker`; web builds a plain hidden `<input type="file">`
 * per the ticket's explicit instruction, rather than `expo-document-
 * picker`'s own web implementation (`ExpoDocumentPicker.web.ts`) — kept as
 * a deliberate, simple DOM primitive matching how this same file's
 * `downloadFile` already reaches for `window.open` directly on web instead
 * of a cross-platform abstraction over it.
 *
 * Resolves to `null` when the user cancels without picking anything
 * (native: `DocumentPickerResult.canceled`). On web there is no reliable
 * cross-browser "the user dismissed the dialog" signal — confirmed by
 * reading `expo-document-picker`'s own `getDocumentAsync` docstring
 * (`node_modules/expo-document-picker/build/index.d.ts`): "The `cancel`
 * event will not be returned in the browser due to platform restrictions
 * and inconsistencies across browsers" — so this promise simply never
 * settles if the web file dialog is dismissed without a selection, matching
 * that same documented limitation rather than guessing at an unreliable
 * `input`/`cancel` DOM event (a real event that DOES exist in modern
 * Chromium/Firefox/Safari, but is too recent/inconsistent to depend on
 * here).
 */
export async function pickAndUpload(targetDir: string): Promise<UploadResult | null> {
  if (Platform.OS === 'web') {
    return pickAndUploadWeb(targetDir);
  }
  return pickAndUploadNative(targetDir);
}

function pickAndUploadWeb(targetDir: string): Promise<UploadResult | null> {
  return new Promise((resolve, reject) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    input.style.display = 'none';

    const cleanup = () => {
      input.parentNode?.removeChild(input);
    };

    input.onchange = () => {
      const fileList = input.files;
      cleanup();
      if (!fileList || fileList.length === 0) {
        resolve(null);
        return;
      }
      uploadToDir(targetDir, Array.from(fileList)).then(resolve, reject);
    };

    document.body.appendChild(input);
    input.click();
  });
}

async function pickAndUploadNative(targetDir: string): Promise<UploadResult | null> {
  const result = await DocumentPicker.getDocumentAsync({ multiple: true, copyToCacheDirectory: true });
  if (result.canceled) return null;

  const parts: UploadPart[] = result.assets.map((asset) => ({
    uri: asset.uri,
    name: asset.name,
    type: asset.mimeType ?? 'application/octet-stream',
  }));
  return uploadToDir(targetDir, parts);
}

/**
 * Web: `window.open` on the download URL directly triggers the browser's
 * own download flow via the server's `Content-Disposition: attachment`
 * header (§5) — no client-side blob/anchor dance needed. `apiBase()`
 * returns `''` on web (`lib/api.ts`), so this resolves to a same-origin
 * relative URL exactly like every other web REST call in this app.
 *
 * Native: uses `expo-file-system`'s NEW (SDK 57) `File`/`Paths` API, NOT
 * the ticket's literal wording ("`expo-file-system` `downloadAsync`") —
 * confirmed by reading `node_modules/expo-file-system/build/
 * legacyWarnings.d.ts`: the top-level `expo-file-system` package's own
 * `downloadAsync` (and `getInfoAsync`/`moveAsync`/etc.) are now deprecated
 * shims whose docstring says outright "This method will throw in runtime."
 * The real, non-throwing legacy implementation still exists, but only
 * under the separate `expo-file-system/legacy` subpath import — since SDK
 * 57's intended replacement is `File.downloadFileAsync(url, destination)`
 * (confirmed via `node_modules/expo-file-system/build/File.d.ts`), that's
 * what's used here instead of reaching for the legacy subpath. Downloads
 * to `Paths.cache` (reclaimable by the OS under storage pressure — fine
 * here, since the file only needs to survive long enough for the
 * subsequent share-sheet hop, not be kept around), then hands off to
 * `expo-sharing`'s share sheet, per the ticket.
 */
export async function downloadFile(path: string): Promise<void> {
  const url = `${apiBase()}/api/files/download?path=${encodeURIComponent(path)}`;

  if (Platform.OS === 'web') {
    window.open(url, '_blank');
    return;
  }

  const filename = path.split('/').pop() || 'download';
  const destination = new File(new Directory(Paths.cache), filename);

  let downloaded: File;
  try {
    // `idempotent: true` so re-downloading the same file (a very likely
    // action — "download it back" is explicitly one of the Tier B phone
    // checks) overwrites the previous cached copy instead of rejecting
    // with `DestinationAlreadyExists`.
    downloaded = await File.downloadFileAsync(url, destination, { idempotent: true });
  } catch (error) {
    // Mirrors `apiFetch`'s error-shape convention (throw `ApiError`) even
    // though this path never goes through `apiFetch` itself — status is
    // unknown here (a native download failure, not an HTTP response
    // `apiFetch` parsed), so `0` is used as a sentinel non-HTTP status.
    throw new ApiError(0, error instanceof Error ? error.message : 'Download failed');
  }

  if (await Sharing.isAvailableAsync()) {
    await Sharing.shareAsync(downloaded.uri);
  }
}
