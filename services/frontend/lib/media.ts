import { apiBase } from './api';
import { authHeaders } from './session';

/**
 * Extension-based media classifier for the files screen's tap-routing
 * decision (M5-02 ticket, §1) — deliberately SEPARATE from
 * `fileDisplay.ts`'s `categoryFor` (MIME-based, server-derived). That
 * existing categorization already covers "what icon/category to show", but
 * the ticket wants a purely client-side, extension-based check here so
 * "should tapping this file open the player?" doesn't depend on the server
 * having correctly guessed a MIME type (`mimetypes.guess_type` can miss or
 * misclassify, e.g. `.mkv` has no registered default MIME type at all on
 * many systems) — extension is the more reliable signal for this specific
 * decision.
 */
export type MediaKind = 'video' | 'audio';

const VIDEO_EXTENSIONS = new Set(['mp4', 'mov', 'm4v', 'webm', 'mkv']);
const AUDIO_EXTENSIONS = new Set(['mp3', 'm4a', 'aac', 'wav', 'ogg', 'flac']);

/** Case-insensitive last dot-segment of `name`, or `null` for "no real
 * extension" (no dot at all, or a trailing dot with nothing after it) —
 * shared by `mediaKind` and `isImageFile` so both apply the exact same
 * "only the LAST dot-segment counts" rule (a multi-dot name like
 * `my.video.file.mp4` is still `"mp4"`, not `"video.file.mp4"`). */
function extensionOf(name: string): string | null {
  const lastDot = name.lastIndexOf('.');
  if (lastDot === -1 || lastDot === name.length - 1) return null;
  return name.slice(lastDot + 1).toLowerCase();
}

/** `null` for anything without a recognized media extension (no extension
 * at all, an unknown extension, or an empty name) — callers treat `null` as
 * "not playable, fall through to the action sheet". Case-insensitive
 * (`.MP4` matches); only the LAST dot-segment counts as the extension, so a
 * multi-dot name like `my.video.file.mp4` is still `"mp4"`, not
 * `"video.file.mp4"`. */
export function mediaKind(name: string): MediaKind | null {
  const extension = extensionOf(name);
  if (extension === null) return null;
  if (VIDEO_EXTENSIONS.has(extension)) return 'video';
  if (AUDIO_EXTENSIONS.has(extension)) return 'audio';
  return null;
}

/** Issue #124: extension-based image check, deliberately separate from
 * `fileDisplay.ts`'s MIME-based `categoryFor` — same rationale as
 * `mediaKind` above (this module's own docstring): a purely client-side,
 * extension-based signal for "should tapping this route straight to a
 * viewer?" that doesn't depend on the server's `mimetypes.guess_type` call
 * having correctly identified the file. Kept as its own function (not
 * folded into `mediaKind`) so `mediaKind`'s existing "image extensions
 * return null" contract — already asserted by `lib/__tests__/media.test.ts`
 * — never changes; every `mediaKind` call site keeps meaning "video or
 * audio", not "any previewable file". */
const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'heic', 'heif', 'svg']);

export function isImageFile(name: string): boolean {
  const extension = extensionOf(name);
  return extension !== null && IMAGE_EXTENSIONS.has(extension);
}

/** Everything the Files tab's tap-routing decision needs, folding
 * `mediaKind` and `isImageFile` into one "does this file get its own
 * in-app viewer?" check (M5-02's video/audio players, or issue #124's
 * image viewer) — `null` means "fall through to the action sheet",
 * exactly like `mediaKind` on its own already meant for video/audio. */
export type PreviewKind = MediaKind | 'image';

export function previewKind(name: string): PreviewKind | null {
  return mediaKind(name) ?? (isImageFile(name) ? 'image' : null);
}

/** Streaming URL for a virtual `path`, hitting
 * `GET /api/platform/files/stream?path=<...>` (Range-request byte streaming —
 * see `services/platform/app/api/external/media.py`). `encodeURIComponent` on the
 * whole path (not just its `/`-separated segments) matches `lib/files.ts`'s
 * `listFiles`/`deletePath` convention for this same query param — the
 * server decodes the full query value back to the original string
 * regardless of how internal `/` characters got percent-encoded. */
export function streamUrl(path: string): string {
  return `${apiBase()}/api/platform/files/stream?path=${encodeURIComponent(path)}`;
}

/** Issue #125: poster-frame thumbnail URL, hitting
 * `GET /api/platform/files/thumbnail?path=<...>` (server-side
 * `ffmpeg`-generated, cached JPEG — see the platform's `app/core/thumbnails.py`).
 * Same `encodeURIComponent`-the-whole-path convention as `streamUrl`
 * above. Callers are expected to have already checked `mediaKind(name)
 * === 'video'` (this module's own extension-based check, not
 * `fileDisplay.ts`'s MIME-based `categoryFor`) before using this — same
 * "extension is the more reliable signal" rationale as `mediaKind`'s own
 * docstring, and it's exactly the check the server itself uses
 * (`is_video_file` in `app/core/thumbnails.py`) to decide whether a given
 * path is even eligible for a thumbnail. */
export function thumbnailUrl(path: string): string {
  return `${apiBase()}/api/platform/files/thumbnail?path=${encodeURIComponent(path)}`;
}

/** Source object for components that load a URL themselves (`Image`,
 * `expo-video`, `expo-audio`) rather than through `apiFetch`: carries the
 * native bearer header; on web it's just `{ uri }` since the cookie rides
 * along. */
export function authedSource(uri: string): { uri: string; headers?: Record<string, string> } {
  const headers = authHeaders();
  return Object.keys(headers).length > 0 ? { uri, headers } : { uri };
}
