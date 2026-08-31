import Ionicons from '@expo/vector-icons/Ionicons';
import { useCallback, useRef, useState, type ReactElement } from 'react';
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
  type NativeSyntheticEvent,
  type TextInputKeyPressEventData,
} from 'react-native';

import { monospaceFontFamily, theme } from '@/lib/theme';
import { useChat, type ChatItem, type ChatToolItem } from '@/lib/useChat';

// Fixed for v1 — real thread switching lands in M3-04 (out of scope here);
// `useChat` already accepts a `threadId` param so this is a one-line swap
// later.
const THREAD_ID = 'default';

const CATEGORY_ICON: Record<ChatToolItem['category'], keyof typeof Ionicons.glyphMap> = {
  file: 'document-text-outline',
  exec: 'terminal-outline',
  plan: 'list-outline',
  other: 'construct-outline',
};

export default function ChatScreen() {
  const { items, sendMessage, busy, connectionState } = useChat(THREAD_ID);
  const [draft, setDraft] = useState('');
  const listRef = useRef<FlatList<ChatItem>>(null);

  const canSend = !busy && draft.trim().length > 0;

  const handleSend = useCallback(() => {
    if (!canSend) return;
    sendMessage(draft.trim());
    setDraft('');
  }, [canSend, draft, sendMessage]);

  // Web: Enter sends, Shift+Enter inserts a newline (standard chat-app
  // convention). Native: Enter/Return always inserts a newline — RN's
  // multiline TextInput already does this by default, so no handling is
  // needed there; this listener is a no-op off web (mirrors the
  // `Platform.OS === 'web'` pattern already used in `lib/api.ts`).
  const handleKeyPress = useCallback(
    (event: NativeSyntheticEvent<TextInputKeyPressEventData>) => {
      if (Platform.OS !== 'web') return;
      const nativeEvent = event.nativeEvent as TextInputKeyPressEventData & {
        shiftKey?: boolean;
        preventDefault?: () => void;
      };
      if (nativeEvent.key === 'Enter' && !nativeEvent.shiftKey) {
        nativeEvent.preventDefault?.();
        handleSend();
      }
    },
    [handleSend],
  );

  const connectionLabel = CONNECTION_LABEL[connectionState];

  return (
    <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={styles.container}>
        {connectionLabel ? (
          <View style={styles.connectionPill}>
            <Text style={styles.connectionPillText}>{connectionLabel}</Text>
          </View>
        ) : null}
        <FlatList
          ref={listRef}
          data={items}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => <ChatItemRow item={item} />}
          contentContainerStyle={styles.listContent}
          style={styles.list}
          // A plain (non-inverted) list + scroll-to-end on growth, rather
          // than `inverted`: `inverted` fights the last item's height
          // changing every token during streaming (content jumps/anchors
          // oddly since the "top" of an inverted list is really the
          // bottom-most rendered item). Scrolling to the end on every size
          // change instead keeps the growing streaming bubble anchored to
          // the bottom exactly the way a chat UI expects.
          onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: true })}
          onLayout={() => listRef.current?.scrollToEnd({ animated: false })}
        />
        <View style={styles.composer}>
          <TextInput
            style={styles.input}
            value={draft}
            onChangeText={setDraft}
            onKeyPress={handleKeyPress}
            placeholder="Message…"
            placeholderTextColor={theme.textMuted}
            multiline
          />
          <Pressable
            style={[styles.sendButton, !canSend && styles.sendButtonDisabled]}
            onPress={handleSend}
            disabled={!canSend}
            accessibilityRole="button"
            accessibilityLabel="Send message"
          >
            <Ionicons name="send" size={18} color={canSend ? theme.text : theme.textMuted} />
          </Pressable>
        </View>
      </View>
    </KeyboardAvoidingView>
  );
}

const CONNECTION_LABEL: Record<ReturnType<typeof useChat>['connectionState'], string | null> = {
  connecting: 'connecting…',
  reconnecting: 'reconnecting…',
  closed: 'disconnected',
  open: null,
};

function ChatItemRow({ item }: { item: ChatItem }): ReactElement {
  switch (item.kind) {
    case 'user':
      return (
        <View style={[styles.bubbleRow, styles.bubbleRowRight]} testID="chat-item-user">
          <View style={[styles.bubble, styles.userBubble]}>
            <Text style={styles.bubbleText}>{item.text}</Text>
          </View>
        </View>
      );
    case 'assistant':
      return (
        <View style={[styles.bubbleRow, styles.bubbleRowLeft]} testID="chat-item-assistant">
          <View style={[styles.bubble, styles.assistantBubble]}>
            <Text style={styles.bubbleText}>
              {item.text}
              {item.streaming ? STREAMING_CURSOR : ''}
            </Text>
          </View>
        </View>
      );
    case 'tool':
      return <ToolItemCard item={item} />;
    case 'error':
      return (
        <View style={[styles.bubbleRow, styles.bubbleRowLeft]} testID="chat-item-error">
          <View style={[styles.bubble, styles.errorBubble]}>
            <Text style={styles.errorText}>{item.message}</Text>
          </View>
        </View>
      );
    default:
      return <></>;
  }
}

// Rendered conditionally at draw time — the stored `text` stays clean so
// state/tests never have to account for a trailing cursor glyph.
const STREAMING_CURSOR = ' ▍';

function ToolItemCard({ item }: { item: ChatToolItem }): ReactElement {
  const [expanded, setExpanded] = useState(false);

  return (
    <View style={styles.toolCard} testID="chat-item-tool">
      <Pressable style={styles.toolHeader} onPress={() => setExpanded((prev) => !prev)}>
        <Ionicons name={CATEGORY_ICON[item.category]} size={16} color={theme.textMuted} />
        <Text style={styles.toolName}>{item.name}</Text>
        {item.status === 'running' ? (
          <ActivityIndicator size="small" color={theme.accent} style={styles.toolStatusIcon} />
        ) : (
          <Ionicons
            name={item.status === 'success' ? 'checkmark-circle' : 'close-circle'}
            size={16}
            color={item.status === 'success' ? theme.success : theme.danger}
            style={styles.toolStatusIcon}
          />
        )}
        <Ionicons
          name={expanded ? 'chevron-up' : 'chevron-down'}
          size={14}
          color={theme.textMuted}
          style={styles.toolChevron}
        />
      </Pressable>
      {expanded ? (
        <View style={styles.toolDetail}>
          <Text style={styles.toolDetailText}>{JSON.stringify(item.args, null, 2)}</Text>
          {item.resultPreview !== undefined ? (
            <Text style={styles.toolDetailText}>{item.resultPreview}</Text>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  flex: {
    flex: 1,
  },
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  connectionPill: {
    alignSelf: 'center',
    marginTop: 8,
    paddingHorizontal: 12,
    paddingVertical: 4,
    borderRadius: 12,
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.border,
  },
  connectionPillText: {
    color: theme.textMuted,
    fontSize: 12,
  },
  list: {
    flex: 1,
  },
  listContent: {
    paddingHorizontal: 12,
    paddingVertical: 12,
    gap: 8,
  },
  bubbleRow: {
    flexDirection: 'row',
  },
  bubbleRowRight: {
    justifyContent: 'flex-end',
  },
  bubbleRowLeft: {
    justifyContent: 'flex-start',
  },
  bubble: {
    maxWidth: '85%',
    borderRadius: 14,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  userBubble: {
    backgroundColor: theme.accent,
  },
  assistantBubble: {
    backgroundColor: theme.surface,
  },
  errorBubble: {
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.danger,
  },
  bubbleText: {
    color: theme.text,
    fontSize: 15,
    lineHeight: 20,
  },
  errorText: {
    color: theme.danger,
    fontSize: 14,
    lineHeight: 19,
  },
  toolCard: {
    alignSelf: 'flex-start',
    maxWidth: '85%',
    backgroundColor: theme.surface,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: theme.border,
    overflow: 'hidden',
  },
  toolHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 10,
    paddingVertical: 8,
  },
  toolName: {
    color: theme.text,
    fontSize: 13,
    flexShrink: 1,
  },
  toolStatusIcon: {
    marginLeft: 4,
  },
  toolChevron: {
    marginLeft: 'auto',
  },
  toolDetail: {
    borderTopWidth: 1,
    borderTopColor: theme.border,
    paddingHorizontal: 10,
    paddingVertical: 8,
    gap: 6,
  },
  toolDetailText: {
    color: theme.textMuted,
    fontFamily: monospaceFontFamily,
    fontSize: 12,
  },
  composer: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderTopWidth: 1,
    borderTopColor: theme.border,
    backgroundColor: theme.bg,
  },
  input: {
    flex: 1,
    maxHeight: 120,
    minHeight: 40,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: theme.border,
    backgroundColor: theme.surface,
    color: theme.text,
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 15,
  },
  sendButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.accent,
  },
  sendButtonDisabled: {
    backgroundColor: theme.surface,
  },
});
