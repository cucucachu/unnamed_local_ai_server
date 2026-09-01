import { Platform } from 'react-native';

import { Directory, File, Paths, UploadType } from 'expo-file-system';
import * as LegacyFileSystem from 'expo-file-system/legacy';
import * as DocumentPicker from 'expo-document-picker';
import * as Sharing from 'expo-sharing';

import { ApiError, apiBase, apiFetch, detailFromBody } from './api';

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

/**
 * One `FormData` part per uploaded file — WEB ONLY (see `uploadToDir`'s own
 * docstring for why native has an entirely separate upload path below,
 * `uploadOneNative`, that never constructs a `FormData` at all). A real DOM
 * `File`/`Blob` (from `<input type="file">`), which the browser's own
 * `fetch`/`FormData` handle natively.
 */
export type UploadPart = File | Blob;

/**
 * WEB upload path — `path` (target dir) + repeated `file` fields per §5,
 * POSTed as `multipart/form-data` via a hand-built `FormData` + `fetch`.
 * Exported separately from `pickAndUpload` below so it's unit-testable
 * without a real file picker (see `lib/__tests__/files.test.ts`).
 *
 * NOT used on native — only `pickAndUploadWeb` calls this; native's
 * `pickAndUploadNative` calls `uploadOneNative` instead. This split (rather
 * than one shared function) exists because of a genuine, confirmed-live-
 * on-device Expo SDK 57 bug, root-caused over several rounds of real
 * Android testing during M3-05:
 *
 * Expo SDK 57 installs its own spec-compliant `fetch` globally
 * (`expo/src/winter/fetch`), which no longer goes through RN's
 * `XMLHttpRequest`-based networking at all. Its `FormData`-to-multipart
 * encoder (`convertFormData.ts`) has a compat shim for RN's classic
 * `FormData` (detected via `typeof formData.getParts === 'function'`) that
 * is fundamentally broken for any `FormData` with more than one part:
 * `formData.entries = function() { return formData.getParts().map(...) }`
 * returns a flat array of bare VALUES (each part's string/file/blob,
 * unwrapped), but the encoder's own consuming loop —
 * `for (const [name, entry] of entries)` — destructures each array
 * element as if it were a `[name, value]` PAIR. For a short string value
 * (e.g. this project's `path` field, often `""` for the workspace root),
 * that silently destructures into `name = undefined, entry = undefined`
 * (an empty string has no characters to iterate), which then fails the
 * encoder's own `typeof entry === 'string' | entry instanceof Blob |
 * 'bytes' in entry` checks and throws `Unsupported FormDataPart
 * implementation` — NOT because of anything about the file part's shape
 * (several file-part shapes were tried and ruled out first: a plain
 * `{ uri, name, type }` object, and a real `expo-file-system` `File`
 * wrapped as `{ file }` per the shim's own `if (part.file) return
 * part.file` branch — both hit the exact same error, for the exact same
 * reason: the `path` STRING field breaks first, before the file field is
 * ever reached).
 *
 * Given that, native doesn't touch `fetch`+`FormData` for uploads at all —
 * see `uploadOneNative`, which uses `expo-file-system`'s own native
 * `File.upload()` (a real multipart implementation in Android/iOS native
 * code, not JS-level `FormData` encoding) instead.
 */
export async function uploadToDir(targetDir: string, parts: UploadPart[]): Promise<UploadResult> {
  const formData = new FormData();
  formData.append('path', targetDir);
  for (const part of parts) {
    formData.append('file', part);
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

/**
 * Copies one picked asset into a file this app fully owns (named
 * `asset.name`, so the upload gets the right filename — `File.name`
 * derives from its URI's basename, and there's no settable `name` to just
 * assign `asset.name` onto directly).
 *
 * `pickAndUploadNative` below deliberately passes `copyToCacheDirectory:
 * false` to the picker, and this copies from the resulting raw `content://`
 * URI directly (via `expo-file-system/legacy`'s `copyAsync`) — NOT from a
 * pre-copied `file://` cache path, despite `copyToCacheDirectory: true`
 * (with a "copy from the cache path" here) being tried FIRST and seeming
 * like the obviously-intended API for exactly this. Four approaches were
 * tried against a real Android device before this one, in order:
 *  1. `copyToCacheDirectory: true` + rename the cached copy in place (new
 *     `File` API) — "Missing 'READ' permission" from `File.rename`.
 *  2. `copyToCacheDirectory: true` + read via `File.bytes()` + write to a
 *     new file (new `File` API) — the exact same "Missing 'READ'
 *     permission", now from `bytes()`.
 *  3. `copyToCacheDirectory: true` + `expo-file-system/legacy`'s
 *     `copyAsync` FROM that same cached path — `java.io.IOException:
 *     Location '.../cache/DocumentPicker/<uuid>.jpg' isn't readable`, a
 *     VERBATIM match for a long-standing, still-open upstream Expo Go bug
 *     (searched and confirmed — see `github.com/expo/expo/issues/21792`,
 *     identical reports spanning SDK 38 through at least SDK 48).
 *  4. Same as #3 plus a retry-with-delay (the most commonly-suggested
 *     mitigation for that bug, on the theory it's a permission-flush
 *     race) — failed identically and deterministically every retry, which
 *     rules out a timing race specifically on this device/Android version
 *     and points at `copyToCacheDirectory: true`'s OWN internal copy step
 *     being the thing that's actually broken here, not a delay before
 *     reading its result.
 * This approach instead never lets the picker attempt that internal copy
 * at all (`copyToCacheDirectory: false`) and does the equivalent copy
 * ourselves, straight from the `content://` URI Android's picker Activity
 * itself returns — the other well-documented workaround for this same
 * class of bug, and structurally different enough (a `ContentResolver`
 * stream copy, not a same-process file-to-file copy of the picker's own
 * output) that it isn't just retrying the same broken step a fifth time.
 */
async function copyPickedAsset(asset: DocumentPicker.DocumentPickerAsset): Promise<File> {
  const destinationUri = `${LegacyFileSystem.cacheDirectory}${asset.name}`;
  await LegacyFileSystem.deleteAsync(destinationUri, { idempotent: true });
  await LegacyFileSystem.copyAsync({ from: asset.uri, to: destinationUri });
  return new File(destinationUri);
}

/**
 * Uploads one already-local `File` via `expo-file-system`'s own native
 * `File.upload()` — a real multipart implementation in Android/iOS native
 * code — instead of `uploadToDir`'s `fetch` + `FormData`. See
 * `uploadToDir`'s docstring for the full story on why native can't use
 * that path at all (a confirmed Expo SDK 57 bug in mixed string+file
 * `FormData` bodies, unrelated to anything fixable in this file).
 *
 * One request per file (`parameters` + a single file, not a repeated
 * `file` field) rather than one combined multipart request for the whole
 * batch — `File.upload()`'s own shape is fundamentally single-file — so
 * `pickAndUploadNative` below calls this once per picked asset and merges
 * the `uploaded` arrays. The server's `POST /api/files/upload` (§5) treats
 * every request as "however many `file` fields are present", so a batch of
 * single-file requests is just as correct as one multi-file request, only
 * less efficient for very large batches (fine for this app's picker-driven
 * upload flow).
 */
async function uploadOneNative(targetDir: string, file: File): Promise<UploadResult> {
  const response = await file.upload(`${apiBase()}/api/files/upload`, {
    uploadType: UploadType.MULTIPART,
    fieldName: 'file',
    parameters: { path: targetDir },
  });

  let body: unknown;
  try {
    body = JSON.parse(response.body);
  } catch {
    body = undefined;
  }

  if (response.status < 200 || response.status >= 300) {
    throw new ApiError(response.status, detailFromBody(body, `Upload failed (${response.status})`));
  }
  return body as UploadResult;
}

async function pickAndUploadNative(targetDir: string): Promise<UploadResult | null> {
  const result = await DocumentPicker.getDocumentAsync({ multiple: true, copyToCacheDirectory: false });
  if (result.canceled) return null;

  const uploaded: string[] = [];
  for (const asset of result.assets) {
    const file = await copyPickedAsset(asset);
    const one = await uploadOneNative(targetDir, file);
    uploaded.push(...one.uploaded);
  }
  return { uploaded };
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
