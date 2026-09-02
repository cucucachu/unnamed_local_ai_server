import Ionicons from '@expo/vector-icons/Ionicons';
import { Modal, Pressable, StyleSheet, Text } from 'react-native';

import type { FileEntry } from '@/lib/files';
import { mediaKind } from '@/lib/media';
import { theme } from '@/lib/theme';

export interface FileActionSheetProps {
  entry: FileEntry | null;
  onClose: () => void;
  onDownload: (entry: FileEntry) => void;
  onRename: (entry: FileEntry) => void;
  onMove: (entry: FileEntry) => void;
  onCopy: (entry: FileEntry) => void;
  onDelete: (entry: FileEntry) => void;
  /** M5-02: opens the media modal for the entry — omitted from the
   * `actions` list entirely (not just disabled) for a non-media entry, per
   * `FileActionSheet`'s own docstring below. Optional so this component still
   * type-checks/renders sensibly for any caller that genuinely never wants
   * a Play action (none exist today, but this keeps the prop's absence a
   * deliberate no-op rather than a required-but-always-passed no-op). */
  onPlay?: (entry: FileEntry) => void;
}

interface ActionSpec {
  key: string;
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  danger?: boolean;
  run: (entry: FileEntry) => void;
}

/**
 * Bottom action sheet for a single file/dir entry — Download, Rename, Move,
 * Copy, Delete (confirm — confirmation itself lives in the caller, per the
 * "Delete (confirm)" ticket wording). A plain custom `Modal`, per the
 * ticket's own stated preference ("use `@expo/react-native-action-sheet` or
 * a simple custom modal — custom modal preferred, zero new deps") over
 * adding a new dependency.
 *
 * M5-02: a "Play" action is prepended — gated on `mediaKind(entry.name)`
 * being non-null, computed HERE (not passed down precomputed) so the
 * caller (`files.tsx`) doesn't need to know this component's own action
 * list shape, matching this component's existing "just hand it an `entry`"
 * calling convention. `onPlay` itself is still owned by the caller (the
 * actual navigation), same division of responsibility as every other
 * action here.
 */
export function FileActionSheet({ entry, onClose, onDownload, onRename, onMove, onCopy, onDelete, onPlay }: FileActionSheetProps) {
  if (!entry) return null;

  const playable = entry.type === 'file' && mediaKind(entry.name) !== null && onPlay !== undefined;

  const actions: ActionSpec[] = [
    ...(playable ? [{ key: 'play', label: 'Play', icon: 'play-circle-outline', run: onPlay! } as ActionSpec] : []),
    { key: 'download', label: 'Download', icon: 'download-outline', run: onDownload },
    { key: 'rename', label: 'Rename', icon: 'create-outline', run: onRename },
    { key: 'move', label: 'Move', icon: 'folder-open-outline', run: onMove },
    { key: 'copy', label: 'Copy', icon: 'copy-outline', run: onCopy },
    { key: 'delete', label: 'Delete', icon: 'trash-outline', danger: true, run: onDelete },
  ];

  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose} testID="file-action-sheet">
      <Pressable style={styles.overlay} onPress={onClose} accessibilityRole="button" accessibilityLabel="Close">
        {/* `stopPropagation` keeps a tap inside the sheet itself from also
            triggering the overlay's own dismiss-on-press-outside handler. */}
        <Pressable style={styles.sheet} onPress={(event) => event.stopPropagation()}>
          <Text style={styles.title} numberOfLines={1}>
            {entry.name}
          </Text>
          {actions.map((action) => (
            <Pressable
              key={action.key}
              style={styles.actionRow}
              onPress={() => {
                onClose();
                action.run(entry);
              }}
              accessibilityRole="button"
              testID={`file-action-${action.key}`}
            >
              <Ionicons name={action.icon} size={20} color={action.danger ? theme.danger : theme.text} />
              <Text style={[styles.actionLabel, action.danger && styles.dangerLabel]}>{action.label}</Text>
            </Pressable>
          ))}
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: theme.surface,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    borderWidth: 1,
    borderColor: theme.border,
    paddingHorizontal: 8,
    paddingTop: 12,
    paddingBottom: 24,
  },
  title: {
    color: theme.textMuted,
    fontSize: 13,
    paddingHorizontal: 12,
    paddingBottom: 8,
  },
  actionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
    paddingHorizontal: 12,
    paddingVertical: 14,
  },
  actionLabel: {
    color: theme.text,
    fontSize: 16,
  },
  dangerLabel: {
    color: theme.danger,
  },
});
