import Ionicons from '@expo/vector-icons/Ionicons';
import { Modal, Pressable, StyleSheet, Text } from 'react-native';

import type { FileEntry } from '@/lib/files';
import { theme } from '@/lib/theme';

export interface FileActionSheetProps {
  entry: FileEntry | null;
  onClose: () => void;
  onDownload: (entry: FileEntry) => void;
  onRename: (entry: FileEntry) => void;
  onMove: (entry: FileEntry) => void;
  onCopy: (entry: FileEntry) => void;
  onDelete: (entry: FileEntry) => void;
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
 * Out of scope per the ticket (M5-02 territory): tapping a file never plays
 * media directly — it always opens this same action sheet regardless of
 * mime type. Left deliberately extensible for that: a future "Play" action
 * would just be one more entry in `buildActions` below, gated on the
 * entry's category, with no change needed to this component's own props or
 * calling convention.
 */
export function FileActionSheet({ entry, onClose, onDownload, onRename, onMove, onCopy, onDelete }: FileActionSheetProps) {
  if (!entry) return null;

  const actions: ActionSpec[] = [
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
