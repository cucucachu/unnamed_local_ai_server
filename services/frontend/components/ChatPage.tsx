import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect } from 'expo-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Animated, BackHandler, Pressable, StyleSheet, View } from 'react-native';

import { ChatHistoryDrawer, ChatHistoryPanel, type ThreadListState } from '@/components/ChatHistoryDrawer';
import { ChatView } from '@/components/ChatView';
import { PageHeader } from '@/components/PageHeader';
import { Toast, useToast } from '@/components/Toast';
import { ApiError } from '@/lib/api';
import { publishThreads } from '@/lib/chatAttention';
import { adoptCreatedThread, openChat, useCurrentChat } from '@/lib/currentChat';
import { theme } from '@/lib/theme';
import { deleteThread, listThreads, renameThread, type Thread } from '@/lib/threads';

/** The history as the home pager's first page (M19-08, on a phone). */
export interface PagedHistory {
  /** The history page's width; the pager is open on it below half that. */
  width: number;
  /** The pager's scroll offset. */
  scrollX: Animated.Value;
  open: () => void;
  close: () => void;
}

/**
 * The Chat page of the home pager (M19-02, a page since M19-07) is a chat:
 * the one last open this app session, or a new empty one on a cold launch
 * (see `lib/currentChat.ts`). The menu button opens the history, whose New
 * chat button starts a new chat. With `paged` (a phone) the history is the
 * page to the left, so a drag right anywhere on the chat opens it, the same
 * native swipe as between the other pages, and this renders both pages;
 * without, it's a drawer over the chat.
 */
export function ChatPage({ width, paged }: { width: number; paged?: PagedHistory }) {
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

  // Paged, the history is open while the pager shows it, however it got there.
  const shownOpen = useRef(false);
  useEffect(() => {
    if (!paged) return;
    const id = paged.scrollX.addListener(({ value }) => {
      const open = value < paged.width / 2;
      if (open === shownOpen.current) return;
      shownOpen.current = open;
      setDrawerOpen(open);
      if (open) void loadThreads();
    });
    return () => paged.scrollX.removeListener(id);
  }, [paged, loadThreads]);

  useEffect(() => {
    if (!paged || !drawerOpen) return;
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      paged.close();
      return true;
    });
    return () => sub.remove();
  }, [paged, drawerOpen]);

  const openDrawer = useCallback(() => {
    if (paged) {
      paged.open();
      return;
    }
    setDrawerOpen(true);
    void loadThreads();
  }, [paged, loadThreads]);

  const closeDrawer = useCallback(() => {
    if (paged) paged.close();
    else setDrawerOpen(false);
  }, [paged]);

  const handleSelect = useCallback(
    (id: string) => {
      closeDrawer();
      openChat(id);
    },
    [closeDrawer],
  );

  const handleNewChat = useCallback(() => {
    closeDrawer();
    openChat(null);
  }, [closeDrawer]);

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

  const shade = useMemo(
    () =>
      paged ? paged.scrollX.interpolate({ inputRange: [0, paged.width], outputRange: [0.5, 0], extrapolate: 'clamp' }) : 0,
    [paged],
  );

  const title = threadId === null ? 'New chat' : (threads.find((t) => t.id === threadId)?.title ?? 'Chat');
  const history = {
    open: drawerOpen,
    threads,
    loadState,
    currentThreadId: threadId,
    onClose: closeDrawer,
    onRetry: () => void loadThreads(),
    onSelect: handleSelect,
    onNewChat: handleNewChat,
    onRename: (thread: Thread, newTitle: string) => void handleRename(thread, newTitle),
    onDelete: (thread: Thread) => void handleDelete(thread),
  };

  const chat = (
    <View style={[styles.container, { width: width || undefined }]} testID="home-page-chat-content">
      <PageHeader
        title={title}
        left={
          <Pressable
            onPress={openDrawer}
            accessibilityRole="button"
            accessibilityLabel="Chat history"
            testID="chat-history-button"
            style={styles.headerButton}
          >
            <Ionicons name="menu" size={24} color={theme.text} />
          </Pressable>
        }
      />
      <ChatView key={key} threadId={threadId} onThreadCreated={adoptCreatedThread} onTurnEnd={loadThreads} />
      {paged ? (
        <Animated.View
          pointerEvents={drawerOpen ? 'auto' : 'none'}
          style={[styles.shade, { opacity: shade }]}
        >
          <Pressable
            style={styles.fill}
            onPress={closeDrawer}
            accessibilityRole="button"
            accessibilityLabel="Close chats"
            testID="chat-drawer-backdrop"
          />
        </Animated.View>
      ) : (
        <ChatHistoryDrawer {...history} />
      )}
      <Toast message={toast} testID="thread-list-toast" />
    </View>
  );

  if (!paged) return chat;
  return (
    <>
      <View style={{ width: paged.width }}>
        <ChatHistoryPanel {...history} />
      </View>
      {chat}
    </>
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
  shade: {
    position: 'absolute',
    top: 0,
    right: 0,
    bottom: 0,
    left: 0,
    backgroundColor: '#000',
  },
  fill: {
    flex: 1,
  },
});
