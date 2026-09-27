import Ionicons from '@expo/vector-icons/Ionicons';
import { useEffect, useRef, useState } from 'react';
import { FlatList, Image, Platform, Pressable, StyleSheet, Text, View } from 'react-native';

import { categoryFor, formatFileSize, iconNameFor } from '@/lib/fileDisplay';
import type { FileEntry, SpaceRole } from '@/lib/files';
import { authedSource, mediaKind, streamUrl, thumbnailUrl } from '@/lib/media';
import { relativeTime } from '@/lib/relativeTime';
import { theme } from '@/lib/theme';

export interface FileListProps {
  entries: FileEntry[];
  onPressEntry: (entry: FileEntry) => void;
  /** Directories only — used by `DestinationPickerModal` (M3-05's move/
   * copy destination browser), which reuses this same component instead of
   * its own list rendering. */
  dirsOnly?: boolean;
  /** Long-press (native) / right-click (web) on a row — triggers the file
   * action sheet on the main files screen. Omitted by the destination
   * picker, which has no per-entry action sheet of its own. */
  onEntryLongPress?: (entry: FileEntry) => void;
  /** Root-relative path of the row to highlight and scroll into view
   * (M9-03 file deep link). */
  highlightedPath?: string | null;
}

/**
 * Directory-listing rows (§5 `FileEntryOut`) — reused by both the main
 * files screen (`src/app/(tabs)/files.tsx`) and the move/copy destination
 * picker (`DestinationPickerModal`, `dirsOnly`). Kept presentation-only per
 * the ticket's "keep props minimal" instruction: no fetching, no empty/
 * error state, no action-sheet UI of its own — those live in whichever
 * screen renders this, matching how `ThreadRow` in `chat/index.tsx` is
 * similarly a pure row renderer for that screen's own `FlatList`.
 *
 * Entries are rendered in the order given — the server already sorts
 * dirs-first, case-insensitive by name (§5's `list_files`); this component
 * never re-sorts.
 */
const ROW_HEIGHT = 56;

export function FileList({
  entries,
  onPressEntry,
  dirsOnly = false,
  onEntryLongPress,
  highlightedPath = null,
}: FileListProps) {
  const listRef = useRef<FlatList<FileEntry>>(null);
  const visibleEntries = dirsOnly ? entries.filter((entry) => entry.type === 'dir') : entries;

  useEffect(() => {
    if (!highlightedPath) return;
    const list = dirsOnly ? entries.filter((entry) => entry.type === 'dir') : entries;
    const index = list.findIndex((entry) => entry.path === highlightedPath);
    if (index < 0) return;
    try {
      listRef.current?.scrollToIndex({ index, animated: true, viewPosition: 0.35 });
    } catch {
      // layout not ready — onScrollToIndexFailed retries below
    }
  }, [highlightedPath, entries, dirsOnly]);

  return (
    <FlatList
      ref={listRef}
      data={visibleEntries}
      keyExtractor={(entry) => entry.path}
      renderItem={({ item }) => (
        <FileRow
          entry={item}
          onPress={onPressEntry}
          onLongPress={onEntryLongPress}
          highlighted={item.path === highlightedPath}
        />
      )}
      getItemLayout={(_data, index) => ({ length: ROW_HEIGHT, offset: ROW_HEIGHT * index, index })}
      onScrollToIndexFailed={({ index }) => {
        setTimeout(() => {
          listRef.current?.scrollToIndex({ index, animated: true, viewPosition: 0.35 });
        }, 80);
      }}
      contentContainerStyle={styles.listContent}
      testID="file-list"
    />
  );
}

/**
 * Issue #124: a small preview thumbnail for image entries, in place of the
 * generic `image-outline` icon every entry otherwise gets from
 * `iconNameFor`. Reuses the exact same `GET /api/platform/files/stream?path=...`
 * URL as the in-app image viewer (`ImageViewer.tsx`'s own docstring has the
 * full "this endpoint streams any file, not just recognized media" citation)
 * — no new thumbnail-generation endpoint, no new server-side dependency,
 * per the issue's own stated preference for a client-side-only first pass.
 *
 * Three explicit states (`loading` / `loaded` / `error`) rather than a bare
 * boolean so a failed image load (corrupt file, permission hiccup, huge
 * file that times out) falls back to the same generic icon every other
 * entry shows, instead of a broken-image glyph — the ticket's own
 * "sensible loading/fallback state" acceptance criterion. `FlatList`
 * itself already limits how many of these are mounted at once (standard
 * virtualization — only rows near the visible window render), so no extra
 * throttling is added here.
 */
function FileThumbnail({ path, name }: { path: string; name: string }) {
  const [status, setStatus] = useState<'loading' | 'loaded' | 'error'>('loading');

  if (status === 'error') {
    return <Ionicons name="image-outline" size={22} color={theme.textMuted} style={styles.icon} />;
  }

  return (
    <View style={styles.thumbnailBox}>
      {status === 'loading' ? <Ionicons name="image-outline" size={16} color={theme.textMuted} /> : null}
      <Image
        source={authedSource(streamUrl(path))}
        style={[styles.thumbnail, status !== 'loaded' && styles.thumbnailHidden]}
        resizeMode="cover"
        onLoad={() => setStatus('loaded')}
        onError={() => setStatus('error')}
        testID="file-thumbnail"
        accessibilityLabel={`${name} thumbnail`}
      />
    </View>
  );
}

/**
 * Issue #125: poster-frame thumbnail for video entries — same three-state
 * (`loading` / `loaded` / `error`) shape and same generic-icon-on-error
 * fallback as `FileThumbnail` above (issue #124's image thumbnail), just
 * pointed at `thumbnailUrl` (the server's `ffmpeg`-generated, cached JPEG
 * — `GET /api/platform/files/thumbnail`) instead of `streamUrl` (the raw file
 * bytes `FileThumbnail` streams directly, which only works for images
 * because a browser/RN `Image` can decode a still image file as-is but
 * can't decode an arbitrary video container into a frame on its own).
 * Falls back to the same `videocam-outline` glyph `iconNameFor` would
 * have shown anyway, so a 404/415/500 from the thumbnail endpoint (e.g. a
 * video `ffmpeg` genuinely can't decode) degrades to exactly what this
 * row looked like before this issue.
 */
function VideoThumbnail({ path, name }: { path: string; name: string }) {
  const [status, setStatus] = useState<'loading' | 'loaded' | 'error'>('loading');

  if (status === 'error') {
    return (
      <Ionicons name="videocam-outline" size={22} color={theme.textMuted} style={styles.icon} />
    );
  }

  return (
    <View style={styles.thumbnailBox}>
      {status === 'loading' ? (
        <Ionicons name="videocam-outline" size={16} color={theme.textMuted} />
      ) : null}
      <Image
        source={authedSource(thumbnailUrl(path))}
        style={[styles.thumbnail, status !== 'loaded' && styles.thumbnailHidden]}
        resizeMode="cover"
        onLoad={() => setStatus('loaded')}
        onError={() => setStatus('error')}
        testID="video-thumbnail"
        accessibilityLabel={`${name} thumbnail`}
      />
    </View>
  );
}

const ROLE_TEXT: Record<SpaceRole, string> = {
  owner: 'Owner',
  editor: 'Editor',
  viewer: 'View only',
};

function FileRow({
  entry,
  onPress,
  onLongPress,
  highlighted,
}: {
  entry: FileEntry;
  onPress: (entry: FileEntry) => void;
  onLongPress?: (entry: FileEntry) => void;
  highlighted: boolean;
}) {
  const iconName = iconNameFor(entry);
  const isImage = entry.type === 'file' && categoryFor(entry) === 'image';
  // Extension-based (`mediaKind`), not `categoryFor`'s MIME-based check —
  // see `VideoThumbnail`'s own docstring and `lib/media.ts`'s `mediaKind`
  // docstring for why: this must agree with the server's own
  // extension-based `is_video_file` (`app/core/thumbnails.py`) eligibility
  // check, which a MIME-based category (e.g. `.mkv`'s often-missing
  // default MIME type) can't be relied on to match.
  const isVideo = entry.type === 'file' && mediaKind(entry.name) === 'video';
  // Space entries (the Files root) show the space's name and the user's role.
  const displayName = entry.label ?? entry.name;
  const subtitle =
    entry.type === 'file'
      ? `${formatFileSize(entry.size)} · ${relativeTime(entry.mtime)}`
      : entry.role
        ? ROLE_TEXT[entry.role]
        : null;

  // Web right-click -> the same action sheet as native long-press (per the
  // ticket). Confirmed real, not guessed: `react-native-web`'s `View` (which
  // `Pressable` builds on) forwards `onContextMenu` straight through to the
  // underlying `<div>` — see `clickProps` in
  // `node_modules/react-native-web/dist/modules/forwardedProps/index.js`,
  // which explicitly includes `onContextMenu` among the DOM event props it
  // passes on. RN's own `PressableProps` type doesn't declare `onContextMenu`
  // at all (it's a web-only DOM event with no native-platform equivalent),
  // hence building this as a separately-typed object and spreading it,
  // rather than trying to pass it as a normal typed prop.
  const webContextMenuProps: Record<string, unknown> =
    Platform.OS === 'web' && onLongPress
      ? {
          onContextMenu: (event: { preventDefault?: () => void }) => {
            event.preventDefault?.();
            onLongPress(entry);
          },
        }
      : {};

  return (
    <Pressable
      style={[styles.row, highlighted && styles.rowHighlighted]}
      onPress={() => onPress(entry)}
      onLongPress={onLongPress ? () => onLongPress(entry) : undefined}
      accessibilityRole="button"
      accessibilityLabel={displayName}
      testID={highlighted ? 'file-entry-highlighted' : 'file-row'}
      {...webContextMenuProps}
    >
      {isImage ? (
        <FileThumbnail path={entry.path} name={entry.name} />
      ) : isVideo ? (
        <VideoThumbnail path={entry.path} name={entry.name} />
      ) : (
        <Ionicons
          name={entry.label ? (entry.path === '/personal' ? 'person-circle-outline' : 'people-outline') : iconName}
          size={22}
          color={entry.type === 'dir' ? theme.accent : theme.textMuted}
          style={styles.icon}
        />
      )}
      <View style={styles.textContainer}>
        <Text style={styles.name} numberOfLines={1}>
          {displayName}
        </Text>
        {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  listContent: {
    paddingVertical: 4,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: theme.border,
    backgroundColor: theme.bg,
    gap: 12,
  },
  rowHighlighted: {
    backgroundColor: theme.surface,
    borderLeftWidth: 3,
    borderLeftColor: theme.accent,
  },
  icon: {
    width: 22,
  },
  thumbnailBox: {
    width: 32,
    height: 32,
    alignItems: 'center',
    justifyContent: 'center',
  },
  thumbnail: {
    position: 'absolute',
    width: 32,
    height: 32,
    borderRadius: 5,
    backgroundColor: theme.surface,
  },
  thumbnailHidden: {
    opacity: 0,
  },
  textContainer: {
    flex: 1,
    gap: 2,
  },
  name: {
    color: theme.text,
    fontSize: 15,
  },
  subtitle: {
    color: theme.textMuted,
    fontSize: 12,
  },
});
