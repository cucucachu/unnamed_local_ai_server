import { apiBase } from './api';

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

/** `null` for anything without a recognized media extension (no extension
 * at all, an unknown extension, or an empty name) — callers treat `null` as
 * "not playable, fall through to the action sheet". Case-insensitive
 * (`.MP4` matches); only the LAST dot-segment counts as the extension, so a
 * multi-dot name like `my.video.file.mp4` is still `"mp4"`, not
 * `"video.file.mp4"`. */
export function mediaKind(name: string): MediaKind | null {
  const lastDot = name.lastIndexOf('.');
  if (lastDot === -1 || lastDot === name.length - 1) return null;

  const extension = name.slice(lastDot + 1).toLowerCase();
  if (VIDEO_EXTENSIONS.has(extension)) return 'video';
  if (AUDIO_EXTENSIONS.has(extension)) return 'audio';
  return null;
}

/** Streaming URL for a workspace-relative `path`, hitting M5-01's
 * `GET /api/media/stream?path=<...>` (Range-request byte streaming — see
 * `services/agent-server/app/api/media.py`). `encodeURIComponent` on the
 * whole path (not just its `/`-separated segments) matches `lib/files.ts`'s
 * `listFiles`/`deletePath` convention for this same query param — the
 * server decodes the full query value back to the original string
 * regardless of how internal `/` characters got percent-encoded. */
export function streamUrl(path: string): string {
  return `${apiBase()}/api/media/stream?path=${encodeURIComponent(path)}`;
}
