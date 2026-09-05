import Ionicons from '@expo/vector-icons/Ionicons';
import { useEffect, useRef } from 'react';
import { FlatList, Platform, Pressable, StyleSheet, Text, View } from 'react-native';

import { formatFileSize, iconNameFor } from '@/lib/fileDisplay';
import type { FileEntry } from '@/lib/files';
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
  /** Workspace-relative path of the row to highlight and scroll into view
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
  const subtitle = entry.type === 'file' ? `${formatFileSize(entry.size)} · ${relativeTime(entry.mtime)}` : null;

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
      accessibilityLabel={entry.name}
      testID={highlighted ? 'file-entry-highlighted' : 'file-row'}
      {...webContextMenuProps}
    >
      <Ionicons
        name={iconName}
        size={22}
        color={entry.type === 'dir' ? theme.accent : theme.textMuted}
        style={styles.icon}
      />
      <View style={styles.textContainer}>
        <Text style={styles.name} numberOfLines={1}>
          {entry.name}
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
