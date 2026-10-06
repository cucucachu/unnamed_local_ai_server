import Ionicons from '@expo/vector-icons/Ionicons';
import { useMemo, useState } from 'react';
import { ActivityIndicator, Alert, FlatList, Modal, Platform, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { PromptModal } from '@/components/PromptModal';
import { Badge } from '@/components/SettingsUI';
import { relativeTime } from '@/lib/relativeTime';
import { theme } from '@/lib/theme';
import type { Thread } from '@/lib/threads';

export type ThreadListState = 'loading' | 'error' | 'done';

export interface ChatHistoryDrawerProps {
  open: boolean;
  threads: Thread[];
  loadState: ThreadListState;
  currentThreadId: string | null;
  onClose: () => void;
  onRetry: () => void;
  onSelect: (threadId: string) => void;
  onRename: (thread: Thread, title: string) => void;
  onDelete: (thread: Thread) => void;
}

/**
 * RN's `Alert.alert` is a no-op on web (`react-native-web`'s `Alert` is
 * `static alert() {}`), so web confirms with `window.confirm`.
 */
function confirmDeleteThread(title: string): Promise<boolean> {
  const message = `Delete "${title}"? This can't be undone.`;
  if (Platform.OS === 'web') return Promise.resolve(window.confirm(message));
  return new Promise((resolve) => {
    Alert.alert('Delete conversation', message, [
      { text: 'Cancel', style: 'cancel', onPress: () => resolve(false) },
      { text: 'Delete', style: 'destructive', onPress: () => resolve(true) },
    ]);
  });
}

/**
 * Chat history (M19-02): slides over the chat from the left. Chats come in
 * the server's order (needs approval, then unread, then recent; M17-10).
 * Long press a chat to rename or delete it.
 */
export function ChatHistoryDrawer({
  open,
  threads,
  loadState,
  currentThreadId,
  onClose,
  onRetry,
  onSelect,
  onRename,
  onDelete,
}: ChatHistoryDrawerProps) {
  const insets = useSafeAreaInsets();
  const [query, setQuery] = useState('');
  const [showRoutineRuns, setShowRoutineRuns] = useState(true);
  const [menuThread, setMenuThread] = useState<Thread | null>(null);
  const [renaming, setRenaming] = useState<Thread | null>(null);

  const hasRoutineRuns = threads.some((t) => t.routine_id);
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return threads.filter(
      (t) => (showRoutineRuns || !t.routine_id) && (!needle || t.title.toLowerCase().includes(needle)),
    );
  }, [threads, query, showRoutineRuns]);

  const handleDelete = async (thread: Thread) => {
    setMenuThread(null);
    if (await confirmDeleteThread(thread.title)) onDelete(thread);
  };

  return (
    <Modal visible={open} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.overlay}>
        <View style={[styles.panel, { paddingTop: insets.top + 8, paddingBottom: insets.bottom }]} testID="chat-drawer">
          <View style={styles.header}>
            <Text style={styles.title}>Chats</Text>
            <Pressable
              onPress={onClose}
              accessibilityRole="button"
              accessibilityLabel="Close chats"
              testID="chat-drawer-close"
              style={styles.iconButton}
            >
              <Ionicons name="close" size={22} color={theme.text} />
            </Pressable>
          </View>
          <TextInput
            style={styles.search}
            value={query}
            onChangeText={setQuery}
            placeholder="Search chats"
            placeholderTextColor={theme.textMuted}
            testID="chat-drawer-search"
          />
          {hasRoutineRuns ? (
            <View style={styles.filters}>
              <Pressable
                onPress={() => setShowRoutineRuns((prev) => !prev)}
                style={[styles.filterChip, showRoutineRuns && styles.filterChipOn]}
                accessibilityRole="switch"
                accessibilityState={{ checked: showRoutineRuns }}
                accessibilityLabel="Show routine runs"
                testID="thread-filter-routine-runs"
              >
                <Ionicons name="alarm-outline" size={14} color={theme.text} />
                <Text style={styles.filterChipText}>Routine runs</Text>
              </Pressable>
            </View>
          ) : null}
          {loadState === 'loading' && threads.length === 0 ? (
            <View style={styles.centered}>
              <ActivityIndicator color={theme.accent} />
            </View>
          ) : loadState === 'error' && threads.length === 0 ? (
            <View style={styles.centered}>
              <Text style={styles.errorText}>Couldn&apos;t load your conversations.</Text>
              <Pressable style={styles.retryButton} onPress={onRetry} accessibilityRole="button" testID="chat-drawer-retry">
                <Text style={styles.retryButtonText}>Retry</Text>
              </Pressable>
            </View>
          ) : shown.length === 0 ? (
            <View style={styles.centered}>
              <Text style={styles.emptyText} testID="chat-drawer-empty">
                {threads.length === 0 ? 'No conversations yet' : 'No matching chats'}
              </Text>
            </View>
          ) : (
            <FlatList
              data={shown}
              keyExtractor={(thread) => thread.id}
              renderItem={({ item }) => (
                <ThreadRow
                  thread={item}
                  current={item.id === currentThreadId}
                  onPress={() => onSelect(item.id)}
                  onLongPress={() => setMenuThread(item)}
                />
              )}
            />
          )}
          {menuThread !== null ? (
            <View style={styles.menu} testID="thread-action-sheet">
              <Text style={styles.menuTitle} numberOfLines={1}>
                {menuThread.title}
              </Text>
              <Pressable
                style={styles.menuItem}
                onPress={() => {
                  setRenaming(menuThread);
                  setMenuThread(null);
                }}
                accessibilityRole="button"
                testID="thread-action-rename"
              >
                <Ionicons name="pencil-outline" size={18} color={theme.text} />
                <Text style={styles.menuItemText}>Rename</Text>
              </Pressable>
              <Pressable
                style={styles.menuItem}
                onPress={() => void handleDelete(menuThread)}
                accessibilityRole="button"
                testID="thread-action-delete"
              >
                <Ionicons name="trash-outline" size={18} color={theme.danger} />
                <Text style={[styles.menuItemText, styles.menuItemDanger]}>Delete</Text>
              </Pressable>
              <Pressable
                style={styles.menuItem}
                onPress={() => setMenuThread(null)}
                accessibilityRole="button"
                testID="thread-action-cancel"
              >
                <Text style={styles.menuItemText}>Cancel</Text>
              </Pressable>
            </View>
          ) : null}
        </View>
        <Pressable
          style={styles.backdrop}
          onPress={onClose}
          accessibilityRole="button"
          accessibilityLabel="Close chats"
          testID="chat-drawer-backdrop"
        />
      </View>
      {renaming !== null ? (
        <PromptModal
          title="Rename chat"
          initialValue={renaming.title}
          submitLabel="Rename"
          onSubmit={(title) => {
            onRename(renaming, title);
            setRenaming(null);
          }}
          onCancel={() => setRenaming(null)}
        />
      ) : null}
    </Modal>
  );
}

function ThreadRow({
  thread,
  current,
  onPress,
  onLongPress,
}: {
  thread: Thread;
  current: boolean;
  onPress: () => void;
  onLongPress: () => void;
}) {
  return (
    <Pressable
      style={[styles.row, current && styles.rowCurrent]}
      onPress={onPress}
      onLongPress={onLongPress}
      testID="thread-row"
      accessibilityRole="button"
      accessibilityLabel={thread.title}
      accessibilityState={{ selected: current }}
    >
      <Text style={[styles.rowTitle, thread.unread && styles.rowTitleUnread]} numberOfLines={1}>
        {thread.title}
      </Text>
      <View style={styles.rowMeta}>
        <Text style={styles.rowTime}>{relativeTime(thread.updated_at)}</Text>
        {thread.routine_id ? <Badge label="Routine" testID={`thread-routine-${thread.id}`} /> : null}
        {thread.needs_approval ? (
          <Badge label="Needs approval" tone="accent" testID={`thread-needs-approval-${thread.id}`} />
        ) : thread.unread ? (
          <Badge label="New" tone="accent" testID={`thread-unread-${thread.id}`} />
        ) : null}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    flexDirection: 'row',
  },
  panel: {
    width: '85%',
    maxWidth: 360,
    backgroundColor: theme.bg,
    borderRightWidth: 1,
    borderRightColor: theme.border,
  },
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingBottom: 8,
  },
  title: {
    color: theme.text,
    fontSize: 20,
    fontWeight: '600',
  },
  iconButton: {
    padding: 6,
  },
  search: {
    marginHorizontal: 16,
    marginBottom: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: theme.border,
    backgroundColor: theme.surface,
    color: theme.text,
    fontSize: 15,
  },
  filters: {
    flexDirection: 'row',
    paddingHorizontal: 16,
    paddingBottom: 8,
  },
  filterChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: theme.border,
  },
  filterChipOn: {
    borderColor: theme.accent,
    backgroundColor: theme.accent,
  },
  filterChipText: {
    color: theme.text,
    fontSize: 13,
  },
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    paddingHorizontal: 24,
  },
  errorText: {
    color: theme.danger,
    fontSize: 15,
    textAlign: 'center',
  },
  emptyText: {
    color: theme.textMuted,
    fontSize: 15,
  },
  retryButton: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 8,
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.border,
  },
  retryButtonText: {
    color: theme.text,
    fontSize: 14,
    fontWeight: '600',
  },
  row: {
    paddingHorizontal: 16,
    paddingVertical: 12,
    gap: 4,
  },
  rowCurrent: {
    backgroundColor: theme.surface,
  },
  rowTitle: {
    color: theme.text,
    fontSize: 15,
  },
  rowTitleUnread: {
    fontWeight: '600',
  },
  rowMeta: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  rowTime: {
    color: theme.textMuted,
    fontSize: 12,
  },
  menu: {
    position: 'absolute',
    left: 12,
    right: 12,
    bottom: 12,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: theme.border,
    backgroundColor: theme.surface,
    paddingVertical: 6,
  },
  menuTitle: {
    color: theme.textMuted,
    fontSize: 13,
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  menuItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  menuItemText: {
    color: theme.text,
    fontSize: 15,
  },
  menuItemDanger: {
    color: theme.danger,
  },
});
