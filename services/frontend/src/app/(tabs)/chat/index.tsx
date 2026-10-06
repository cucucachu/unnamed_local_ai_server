import Ionicons from '@expo/vector-icons/Ionicons';
import { Stack, useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';

import { ChatHistoryDrawer, type ThreadListState } from '@/components/ChatHistoryDrawer';
import { ChatView } from '@/components/ChatView';
import { SwipeToOpen } from '@/components/SwipeToOpen';
import { Toast, useToast } from '@/components/Toast';
import { ApiError } from '@/lib/api';
import { publishThreads } from '@/lib/chatAttention';
import { adoptCreatedThread, openChat, useCurrentChat } from '@/lib/currentChat';
import { theme } from '@/lib/theme';
import { deleteThread, listThreads, renameThread, type Thread } from '@/lib/threads';

/**
 * The Chat tab (M19-02) is a chat: the one last open this app session, or a
 * new empty one on a cold launch (see `lib/currentChat.ts`). The menu button
 * (or, on a phone, a swipe right) opens the history drawer; + starts a new
 * chat. `/chat/<id>` deep links land here through `[threadId].tsx`.
 */
export default function ChatTabScreen() {
  const { threadId, key } = useCurrentChat();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [loadState, setLoadState] = useState<ThreadListState>('loading');
  const { message: toast, showToast } = useToast();

  const loadThreads = useCallback(async () => {
    try {
      const fetched = await listThreads();
      setThreads(fetched);
      publishThreads(fetched);
      setLoadState('done');
    } catch {
      setLoadState('error');
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      void loadThreads();
    }, [loadThreads]),
  );

  const openDrawer = useCallback(() => {
    setDrawerOpen(true);
    void loadThreads();
  }, [loadThreads]);

  const handleSelect = useCallback((id: string) => {
    setDrawerOpen(false);
    openChat(id);
  }, []);

  const handleNewChat = useCallback(() => {
    setDrawerOpen(false);
    openChat(null);
  }, []);

  const handleRename = useCallback(
    async (thread: Thread, title: string) => {
      setThreads((prev) => prev.map((t) => (t.id === thread.id ? { ...t, title } : t)));
      try {
        const updated = await renameThread(thread.id, title);
        setThreads((prev) => prev.map((t) => (t.id === thread.id ? { ...t, title: updated.title } : t)));
      } catch (error) {
        setThreads((prev) => prev.map((t) => (t.id === thread.id ? { ...t, title: thread.title } : t)));
        showToast(error instanceof ApiError ? error.detail : 'Failed to rename conversation');
      }
    },
    [showToast],
  );

  const handleDelete = useCallback(
    async (thread: Thread) => {
      const indexBeforeRemoval = threads.findIndex((t) => t.id === thread.id);
      setThreads((prev) => prev.filter((t) => t.id !== thread.id));
      if (thread.id === threadId) openChat(null);
      try {
        await deleteThread(thread.id);
      } catch (error) {
        setThreads((prev) => {
          if (prev.some((t) => t.id === thread.id)) return prev;
          const restored = [...prev];
          restored.splice(Math.min(indexBeforeRemoval, restored.length), 0, thread);
          return restored;
        });
        showToast(error instanceof ApiError ? error.detail : 'Failed to delete conversation');
      }
    },
    [threads, threadId, showToast],
  );

  const title = threadId === null ? 'New chat' : (threads.find((t) => t.id === threadId)?.title ?? 'Chat');

  return (
    <View style={styles.container}>
      <Stack.Screen
        options={{
          title,
          headerLeft: () => (
            <Pressable
              onPress={openDrawer}
              accessibilityRole="button"
              accessibilityLabel="Chat history"
              testID="chat-history-button"
              style={styles.headerButton}
            >
              <Ionicons name="menu" size={24} color={theme.text} />
            </Pressable>
          ),
          headerRight: () => (
            <Pressable
              onPress={handleNewChat}
              accessibilityRole="button"
              accessibilityLabel="New chat"
              testID="new-chat-header-button"
              style={styles.headerButton}
            >
              <Ionicons name="add" size={26} color={theme.text} />
            </Pressable>
          ),
        }}
      />
      <SwipeToOpen onOpen={openDrawer}>
        <ChatView key={key} threadId={threadId} onThreadCreated={adoptCreatedThread} onTurnEnd={loadThreads} />
      </SwipeToOpen>
      <ChatHistoryDrawer
        open={drawerOpen}
        threads={threads}
        loadState={loadState}
        currentThreadId={threadId}
        onClose={() => setDrawerOpen(false)}
        onRetry={() => void loadThreads()}
        onSelect={handleSelect}
        onRename={(thread, newTitle) => void handleRename(thread, newTitle)}
        onDelete={(thread) => void handleDelete(thread)}
      />
      <Toast message={toast} testID="thread-list-toast" />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  headerButton: {
    padding: 6,
    marginHorizontal: 4,
  },
});
