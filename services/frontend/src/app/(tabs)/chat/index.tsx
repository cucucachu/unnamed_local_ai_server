import Ionicons from '@expo/vector-icons/Ionicons';
import { Stack, useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, FlatList, Platform, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { Swipeable } from 'react-native-gesture-handler';

import { ApiError } from '@/lib/api';
import { relativeTime } from '@/lib/relativeTime';
import { theme } from '@/lib/theme';
import { createThread, deleteThread, listThreads, type Thread } from '@/lib/threads';

/**
 * Thread-list screen (M3-04) — the new `index` route of the `chat` stack
 * (see `chat/_layout.tsx`); `[threadId].tsx` is the M2-06 chat screen this
 * navigates into.
 */

type LoadState = 'loading' | 'error' | 'done';

/**
 * RN's `Alert.alert` has NO real implementation on web — confirmed by
 * reading `react-native-web`'s own source
 * (`node_modules/react-native-web/src/exports/Alert/index.js`): the whole
 * class is `static alert() {}`, a no-op. Hence the ticket's explicit
 * "`Alert` + `window.confirm` fallback" — this isn't a defensive nicety,
 * it's the only way to get an actual confirm dialog on web at all.
 */
function confirmDeleteThread(title: string): Promise<boolean> {
  const message = `Delete "${title}"? This can't be undone.`;

  if (Platform.OS === 'web') {
    return Promise.resolve(window.confirm(message));
  }

  return new Promise((resolve) => {
    Alert.alert('Delete conversation', message, [
      { text: 'Cancel', style: 'cancel', onPress: () => resolve(false) },
      { text: 'Delete', style: 'destructive', onPress: () => resolve(true) },
    ]);
  });
}

export default function ThreadListScreen() {
  const router = useRouter();
  const [threads, setThreads] = useState<Thread[]>([]);
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [refreshing, setRefreshing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = useCallback((message: string) => {
    if (toastTimerRef.current !== null) clearTimeout(toastTimerRef.current);
    setToast(message);
    toastTimerRef.current = setTimeout(() => setToast(null), 3000);
  }, []);

  useEffect(() => {
    return () => {
      if (toastTimerRef.current !== null) clearTimeout(toastTimerRef.current);
    };
  }, []);

  const loadThreads = useCallback(async () => {
    try {
      const fetched = await listThreads();
      setThreads(fetched);
      setLoadState('done');
    } catch {
      setLoadState('error');
    }
  }, []);

  // Spec: "auto-refresh on screen focus". `useFocusEffect` (from
  // `expo-router` directly — confirmed real by reading
  // `node_modules/expo-router/build/exports.d.ts`, which re-exports it from
  // `./useFocusEffect`, and by this screen actually building/navigating —
  // rather than assumed to come from `@react-navigation/native`, which
  // isn't even installed as a direct or hoisted dependency in this repo's
  // `node_modules`) also covers the initial mount fetch, so no separate
  // `useEffect(() => { loadThreads() }, [])` is needed alongside it.
  useFocusEffect(
    useCallback(() => {
      loadThreads();
    }, [loadThreads]),
  );

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    listThreads()
      .then((fetched) => {
        setThreads(fetched);
        setLoadState('done');
      })
      .catch(() => showToast('Failed to refresh conversations'))
      .finally(() => setRefreshing(false));
  }, [showToast]);

  const handleNewChat = useCallback(async () => {
    if (creating) return;
    setCreating(true);
    try {
      const thread = await createThread();
      router.push({ pathname: '/chat/[threadId]', params: { threadId: thread.id } });
    } catch (error) {
      showToast(error instanceof ApiError ? error.detail : 'Failed to create a new chat');
    } finally {
      setCreating(false);
    }
  }, [creating, router, showToast]);

  const handleDelete = useCallback(
    async (thread: Thread) => {
      const indexBeforeRemoval = threads.findIndex((t) => t.id === thread.id);
      setThreads((prev) => prev.filter((t) => t.id !== thread.id));
      try {
        await deleteThread(thread.id);
      } catch (error) {
        // Restore + toast on failure, per spec.
        setThreads((prev) => {
          if (prev.some((t) => t.id === thread.id)) return prev;
          const restored = [...prev];
          restored.splice(Math.min(indexBeforeRemoval, restored.length), 0, thread);
          return restored;
        });
        showToast(error instanceof ApiError ? error.detail : 'Failed to delete conversation');
      }
    },
    [threads, showToast],
  );

  return (
    <View style={styles.container}>
      <Stack.Screen
        options={{
          headerRight: () => (
            <Pressable
              onPress={handleNewChat}
              disabled={creating}
              accessibilityRole="button"
              accessibilityLabel="New chat"
              testID="new-chat-header-button"
              style={styles.headerButton}
            >
              {creating ? (
                <ActivityIndicator size="small" color={theme.text} />
              ) : (
                <Ionicons name="add" size={26} color={theme.text} />
              )}
            </Pressable>
          ),
        }}
      />

      {loadState === 'loading' ? (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color={theme.accent} />
        </View>
      ) : loadState === 'error' ? (
        <View style={styles.centered}>
          <Text style={styles.errorText}>Couldn&apos;t load your conversations.</Text>
          <Pressable style={styles.retryButton} onPress={loadThreads} accessibilityRole="button">
            <Text style={styles.retryButtonText}>Retry</Text>
          </Pressable>
        </View>
      ) : threads.length === 0 ? (
        <View style={styles.centered}>
          <Text style={styles.emptyText}>No conversations yet</Text>
          <Pressable
            style={styles.newChatButton}
            onPress={handleNewChat}
            disabled={creating}
            accessibilityRole="button"
            accessibilityLabel="New chat"
            testID="new-chat-empty-button"
          >
            <Text style={styles.newChatButtonText}>New chat</Text>
          </Pressable>
        </View>
      ) : (
        <FlatList
          data={threads}
          keyExtractor={(thread) => thread.id}
          renderItem={({ item }) => <ThreadRow thread={item} onDelete={handleDelete} />}
          contentContainerStyle={styles.listContent}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={theme.accent} />}
        />
      )}

      {toast ? (
        <View style={styles.toast} testID="thread-list-toast">
          <Text style={styles.toastText}>{toast}</Text>
        </View>
      ) : null}
    </View>
  );
}

function ThreadRow({ thread, onDelete }: { thread: Thread; onDelete: (thread: Thread) => void }) {
  const router = useRouter();
  const swipeableRef = useRef<Swipeable>(null);

  const handlePress = useCallback(() => {
    router.push({ pathname: '/chat/[threadId]', params: { threadId: thread.id } });
  }, [router, thread.id]);

  const handleLongPress = useCallback(async () => {
    const confirmed = await confirmDeleteThread(thread.title);
    if (confirmed) onDelete(thread);
  }, [onDelete, thread]);

  const rowContent = (
    <Pressable
      style={styles.row}
      onPress={handlePress}
      // Long-press-to-delete is the WEB affordance (spec: "long-press ->
      // confirm dialog on web"); native uses the swipe gesture below
      // instead, so this handler is a no-op there (native still gets
      // `onLongPress` wired up harmlessly if this branch weren't gated, but
      // gating it avoids a redundant second delete path on native).
      onLongPress={Platform.OS === 'web' ? handleLongPress : undefined}
      testID="thread-row"
      accessibilityRole="button"
      accessibilityLabel={thread.title}
    >
      <View style={styles.rowTextContainer}>
        <Text style={styles.rowTitle} numberOfLines={1}>
          {thread.title}
        </Text>
        <Text style={styles.rowTime}>{relativeTime(thread.updated_at)}</Text>
      </View>
    </Pressable>
  );

  // `Swipeable` (from `react-native-gesture-handler`, already a direct
  // dependency — see `package.json`) rather than
  // `ReanimatedSwipeable`/`react-native-reanimated`: confirmed by reading
  // `node_modules/react-native-gesture-handler/src/index.ts` that the
  // plain `Swipeable` export still exists (deprecated in favor of the
  // Reanimated version, but functional) and is built on RN's own
  // `Animated` API, not Reanimated — which isn't a direct dependency of
  // this project (only present transitively via some other package) — so
  // using it avoids adding a new direct dependency for this ticket.
  // Native-only: swipe gestures don't apply on web, and `Swipeable` itself
  // is unnecessary chrome there (long-press covers deletion on web).
  if (Platform.OS === 'web') return rowContent;

  return (
    <Swipeable
      ref={swipeableRef}
      overshootRight={false}
      renderRightActions={() => (
        <Pressable
          style={styles.deleteAction}
          onPress={() => {
            swipeableRef.current?.close();
            onDelete(thread);
          }}
          accessibilityRole="button"
          accessibilityLabel={`Delete ${thread.title}`}
        >
          <Ionicons name="trash-outline" size={20} color="#ffffff" />
        </Pressable>
      )}
    >
      {rowContent}
    </Swipeable>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  headerButton: {
    padding: 6,
    marginRight: 4,
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
    fontSize: 16,
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
  newChatButton: {
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 10,
    backgroundColor: theme.accent,
  },
  newChatButtonText: {
    color: theme.text,
    fontSize: 15,
    fontWeight: '600',
  },
  listContent: {
    paddingVertical: 4,
  },
  row: {
    paddingHorizontal: 16,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: theme.border,
    backgroundColor: theme.bg,
  },
  rowTextContainer: {
    gap: 4,
  },
  rowTitle: {
    color: theme.text,
    fontSize: 16,
  },
  rowTime: {
    color: theme.textMuted,
    fontSize: 13,
  },
  deleteAction: {
    width: 76,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.danger,
  },
  toast: {
    position: 'absolute',
    bottom: 20,
    left: 16,
    right: 16,
    backgroundColor: theme.surface,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: theme.border,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  toastText: {
    color: theme.text,
    fontSize: 14,
    textAlign: 'center',
  },
});
