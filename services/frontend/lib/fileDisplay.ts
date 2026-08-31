import Ionicons from '@expo/vector-icons/Ionicons';

/**
 * Presentation helpers for file/dir entries (§5 `FileEntryOut`) — pure
 * functions kept separate from `lib/files.ts` (the REST client itself) so
 * `FileList`/`files.tsx` can import display logic without pulling in
 * network code, and so both halves stay independently unit-testable per
 * the ticket ("size-formatting helper; icon-by-mime mapping" as its own
 * test target).
 */

const SIZE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB'] as const;

/** Human-readable file size, binary (1024-based) units, no date/size lib
 * per this ticket's own "no new deps" spirit (mirrors `relativeTime.ts`'s
 * M3-04 precedent of a tiny hand-rolled helper over pulling in a library
 * for something this small). One decimal place, dropped when it would just
 * be ".0" (`"1 MB"`, not `"1.0 MB"`); caps at TB rather than growing to PB/
 * EB — a self-hosted single-user workspace realistically never lists a
 * file large enough for that boundary to matter. */
export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '';
  if (bytes < 1024) return `${bytes} B`;

  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < SIZE_UNITS.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }

  const rounded = Math.round(value * 10) / 10;
  const display = rounded % 1 === 0 ? rounded.toFixed(0) : rounded.toFixed(1);
  return `${display} ${SIZE_UNITS[unitIndex]}`;
}

export type FileCategory = 'folder' | 'image' | 'video' | 'audio' | 'text' | 'archive' | 'other';

/** MIME types `mimetypes.guess_type` (used server-side, see `files.py`)
 * assigns to common archive formats — not a MIME prefix like `image/*`, so
 * these need an explicit lookup table rather than a `startsWith` check. */
const ARCHIVE_MIME_TYPES = new Set([
  'application/zip',
  'application/x-tar',
  'application/x-gzip',
  'application/gzip',
  'application/x-bzip2',
  'application/x-7z-compressed',
  'application/x-rar-compressed',
  'application/vnd.rar',
  'application/java-archive',
]);

/** Category for one entry — `type: "dir"` always wins regardless of `mime`
 * (directories never carry a meaningful MIME type from the server anyway;
 * see `FileEntryOut.mime` only being populated via `mimetypes.guess_type`
 * for files in `files.py`'s `_entry_out`). */
export function categoryFor(entry: { type: 'file' | 'dir'; mime: string | null }): FileCategory {
  if (entry.type === 'dir') return 'folder';
  const mime = entry.mime;
  if (!mime) return 'other';
  if (mime.startsWith('image/')) return 'image';
  if (mime.startsWith('video/')) return 'video';
  if (mime.startsWith('audio/')) return 'audio';
  if (mime.startsWith('text/')) return 'text';
  if (ARCHIVE_MIME_TYPES.has(mime)) return 'archive';
  return 'other';
}

const CATEGORY_ICON: Record<FileCategory, keyof typeof Ionicons.glyphMap> = {
  folder: 'folder',
  image: 'image-outline',
  video: 'videocam-outline',
  audio: 'musical-notes-outline',
  text: 'document-text-outline',
  archive: 'archive-outline',
  other: 'document-outline',
};

/** Icon name (verified real `Ionicons` glyph names — grepped against
 * `node_modules/@expo/vector-icons/build/vendor/react-native-vector-icons/
 * glyphmaps/Ionicons.json` for this ticket rather than guessed) for one
 * entry, by type/mime. */
export function iconNameFor(entry: { type: 'file' | 'dir'; mime: string | null }): keyof typeof Ionicons.glyphMap {
  return CATEGORY_ICON[categoryFor(entry)];
}
