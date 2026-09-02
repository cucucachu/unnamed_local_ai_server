import Ionicons from '@expo/vector-icons/Ionicons';
import { useLocalSearchParams } from 'expo-router';
import { useCallback, useRef, useState, type ReactElement } from 'react';
import {
  ActivityIndicator,
  Dimensions,
  KeyboardAvoidingView,
  FlatList,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  type NativeSyntheticEvent,
  type TextInputKeyPressEventData,
} from 'react-native';

import { parseExecResult, type ParsedExecResult } from '@/lib/execResult';
import { monospaceFontFamily, theme } from '@/lib/theme';
import { useChat, type ChatItem, type ChatToolItem } from '@/lib/useChat';

const CATEGORY_ICON: Record<ChatToolItem['category'], keyof typeof Ionicons.glyphMap> = {
  file: 'document-text-outline',
  exec: 'terminal-outline',
  plan: 'list-outline',
  other: 'construct-outline',
};

/** M2-06's screen, parameterized by `threadId` (M3-04) instead of the old
 * hardcoded `THREAD_ID = 'default'`. `useLocalSearchParams` (not
 * `useGlobalSearchParams`) is the right hook here per expo-router's own
 * distinction: this screen only ever cares about ITS OWN route's
 * `[threadId]` segment, never a parent/sibling route's params. */
export default function ChatScreen() {
  const { threadId } = useLocalSearchParams<{ threadId: string }>();
  const { items, sendMessage, busy, connectionState, hydrationState, retryHydration } = useChat(threadId);
  const [draft, setDraft] = useState('');
  const listRef = useRef<FlatList<ChatItem>>(null);

  const canSend = !busy && hydrationState === 'done' && draft.trim().length > 0;

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

  // Spec: "Show a spinner until hydration completes; hydration failure ->
  // error banner with retry, socket not opened." These two early returns
  // (before the composer/list render at all) are what make that literal:
  // `useChat` itself never opens the socket while `hydrationState !==
  // 'done'` (see `lib/useChat.ts`), and this screen mirrors that by not
  // rendering the composer/message list until then either.
  if (hydrationState === 'loading') {
    return (
      <View style={[styles.container, styles.centered]}>
        <ActivityIndicator size="large" color={theme.accent} />
      </View>
    );
  }

  if (hydrationState === 'error') {
    return (
      <View style={[styles.container, styles.centered]}>
        <Text style={styles.hydrationErrorText}>Couldn&apos;t load this conversation.</Text>
        <Pressable
          style={styles.retryButton}
          onPress={retryHydration}
          accessibilityRole="button"
          accessibilityLabel="Retry loading conversation"
        >
          <Text style={styles.retryButtonText}>Retry</Text>
        </Pressable>
      </View>
    );
  }

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

/** Defensive: `item.args` is typed `Record<string, unknown>`, so
 * `args.command` isn't guaranteed to be a string at the type level even
 * though the server always sends one for `execute_code`. Falls back to an
 * empty collapsed-header line (never throws/crashes) if it isn't. */
function firstLine(command: unknown): string {
  if (typeof command !== 'string') return '';
  const newlineIndex = command.indexOf('\n');
  return newlineIndex === -1 ? command : command.slice(0, newlineIndex);
}

interface ExecChipInfo {
  text: string;
  color: string;
}

/** Chip precedence: a timed-out run gets its own distinct chip regardless
 * of its (GNU-`timeout`-mandated) exit code — see
 * `services/code-exec-manager/app/sessions.py`'s `GNU_TIMEOUT_EXIT_CODE`
 * (124). `null` (parse failure — e.g. an `execute_code failed: ...`
 * transport-error string, which never matches the `exit_code: N` shape)
 * means "not a real exec result": caller falls back to the generic
 * checkmark/x icon in that case. */
function execChipInfo(parsed: ParsedExecResult): ExecChipInfo | null {
  if (parsed.exitCode === null) return null;
  if (parsed.timedOut) return { text: '⏱ timed out', color: theme.accent };
  if (parsed.exitCode === 0) return { text: '✓ exit 0', color: theme.success };
  return { text: `✗ exit ${parsed.exitCode}`, color: theme.danger };
}

const TRUNCATED_MARKER = '[output truncated]';

/** Splits the trailing `\n[output truncated]` line (if present) off the
 * parsed body so it can be styled dim/italic separately from the raw
 * stdout/stderr text, per spec. */
function splitTruncatedMarker(body: string): { main: string; truncated: boolean } {
  const suffix = `\n${TRUNCATED_MARKER}`;
  if (body.endsWith(suffix)) {
    return { main: body.slice(0, -suffix.length), truncated: true };
  }
  return { main: body, truncated: false };
}

/** Caps the scrollable stdout/stderr block at ~40% of the window/screen
 * height (spec: "capped at roughly 40% of screen height"). A `Dimensions`-
 * based percentage (over a fixed pixel value) is the pragmatic RN Web +
 * native choice here: it scales sensibly across phone/tablet/desktop
 * viewports without a breakpoint table, and `Dimensions.get` is read at
 * render time (not hoisted to a module constant) so it reflects the
 * current window on web resize / native orientation change — no dedicated
 * resize listener needed for what's just a soft cap on a `ScrollView`. */
function getExecOutputMaxHeight(): number {
  return Dimensions.get('window').height * 0.4;
}

function ExecToolDetail({ item, parsed }: { item: ChatToolItem; parsed: ParsedExecResult | null }): ReactElement {
  const commandText =
    typeof item.args.command === 'string' ? item.args.command : JSON.stringify(item.args, null, 2);
  const split = parsed !== null ? splitTruncatedMarker(parsed.body) : null;

  return (
    <View style={styles.toolDetail}>
      <Text style={styles.toolDetailText}>{commandText}</Text>
      {split !== null ? (
        <ScrollView style={[styles.execOutputScroll, { maxHeight: getExecOutputMaxHeight() }]}>
          <Text style={styles.toolDetailText}>{split.main}</Text>
          {split.truncated ? <Text style={styles.execTruncatedText}>{TRUNCATED_MARKER}</Text> : null}
        </ScrollView>
      ) : null}
    </View>
  );
}

function ToolItemCard({ item }: { item: ChatToolItem }): ReactElement {
  const [expanded, setExpanded] = useState(false);
  const isExec = item.category === 'exec';
  const execParsed = isExec && item.resultPreview !== undefined ? parseExecResult(item.resultPreview) : null;
  const chip = execParsed !== null ? execChipInfo(execParsed) : null;

  return (
    <View style={styles.toolCard} testID="chat-item-tool">
      <Pressable style={styles.toolHeader} onPress={() => setExpanded((prev) => !prev)} testID="chat-item-tool-header">
        <Ionicons name={CATEGORY_ICON[item.category]} size={16} color={theme.textMuted} />
        {isExec ? (
          <Text style={styles.toolName} numberOfLines={1} ellipsizeMode="tail">
            {firstLine(item.args.command)}
          </Text>
        ) : (
          <Text style={styles.toolName}>{item.name}</Text>
        )}
        {item.status === 'running' ? (
          <ActivityIndicator size="small" color={theme.accent} style={styles.toolStatusIcon} />
        ) : isExec && chip !== null ? (
          <View style={[styles.execChip, { borderColor: chip.color }]}>
            <Text style={[styles.execChipText, { color: chip.color }]}>{chip.text}</Text>
          </View>
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
        isExec ? (
          <ExecToolDetail item={item} parsed={execParsed} />
        ) : (
          <View style={styles.toolDetail}>
            <Text style={styles.toolDetailText}>{JSON.stringify(item.args, null, 2)}</Text>
            {item.resultPreview !== undefined ? (
              <Text style={styles.toolDetailText}>{item.resultPreview}</Text>
            ) : null}
          </View>
        )
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
  centered: {
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
  },
  hydrationErrorText: {
    color: theme.danger,
    fontSize: 15,
    textAlign: 'center',
    paddingHorizontal: 24,
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
  execChip: {
    marginLeft: 4,
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 10,
    borderWidth: 1,
  },
  execChipText: {
    fontSize: 12,
    fontWeight: '600',
  },
  execOutputScroll: {
    borderRadius: 6,
    backgroundColor: theme.bg,
    padding: 6,
  },
  execTruncatedText: {
    color: theme.textMuted,
    fontFamily: monospaceFontFamily,
    fontSize: 12,
    fontStyle: 'italic',
    marginTop: 2,
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
