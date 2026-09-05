import Ionicons from '@expo/vector-icons/Ionicons';
import { useLocalSearchParams } from 'expo-router';
import { useCallback, useRef, useState, type ReactElement } from 'react';
import {
  ActivityIndicator,
  Dimensions,
  KeyboardAvoidingView,
  FlatList,
  Linking,
  Modal,
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

import { Markdown } from '@/components/Markdown';
import { TurnActivityPanel } from '@/components/TurnActivityPanel';
import { useSettings } from '@/components/SettingsProvider';
import { copyToClipboard } from '@/lib/clipboard';
import { formatDuration } from '@/lib/chatTurns';
import { parseExecResult, type ParsedExecResult } from '@/lib/execResult';
import { monospaceFontFamily, theme } from '@/lib/theme';
import type { ApprovalDecision, EditMode } from '@/lib/chatSocket';
import type { ThreadBranchPoint } from '@/lib/threads';
import {
  useChat,
  type ChatAssistantItem,
  type ChatItem,
  type ChatToolItem,
  type ChatTurn,
  type ChatUserItem,
  type PendingApproval,
  type PendingApprovalAction,
} from '@/lib/useChat';
import {
  hostnameFromUrl,
  parseFetchResult,
  parseSearchResults,
  type ParsedFetchResult,
  type ParsedSearchResults,
} from '@/lib/webResult';

const CATEGORY_ICON: Record<ChatToolItem['category'], keyof typeof Ionicons.glyphMap> = {
  file: 'document-text-outline',
  exec: 'terminal-outline',
  plan: 'list-outline',
  // Default icon for the `web` category — overridden to `search-outline`
  // specifically for `web_search` (see `toolIconName` below); `web_fetch`
  // keeps this `globe-outline` default.
  web: 'globe-outline',
  other: 'construct-outline',
};

/** M2-06's screen, parameterized by `threadId` (M3-04) instead of the old
 * hardcoded `THREAD_ID = 'default'`. `useLocalSearchParams` (not
 * `useGlobalSearchParams`) is the right hook here per expo-router's own
 * distinction: this screen only ever cares about ITS OWN route's
 * `[threadId]` segment, never a parent/sibling route's params. */
export default function ChatScreen() {
  const { threadId } = useLocalSearchParams<{ threadId: string }>();
  const {
    turns,
    sendMessage,
    stopTurn,
    busy,
    connectionState,
    hydrationState,
    retryHydration,
    pendingApproval,
    respondToApproval,
    branches,
    switchBranch,
  } = useChat(threadId);
  const { settings } = useSettings();
  const defaultEditMode: EditMode = settings?.edit_mode_default ?? 'truncate';
  const [draft, setDraft] = useState('');
  const [editingItem, setEditingItem] = useState<ChatUserItem | null>(null);
  const [editMode, setEditMode] = useState<EditMode>(defaultEditMode);
  const [actionMenu, setActionMenu] = useState<
    { kind: 'user'; item: ChatUserItem } | { kind: 'assistant'; item: ChatAssistantItem } | null
  >(null);
  const listRef = useRef<FlatList<ChatTurn>>(null);

  // M8-03: the composer/Send button is disabled while an approval is
  // pending, in addition to the existing `busy` disable from M8-01.
  const canSend = !busy && pendingApproval === null && hydrationState === 'done' && draft.trim().length > 0;
  const menuDisabled = busy || pendingApproval !== null;

  const handleSend = useCallback(() => {
    if (!canSend) return;
    const text = draft.trim();
    if (editingItem !== null) {
      sendMessage(text, { replaceFromMessageId: editingItem.id, mode: editMode });
      setEditingItem(null);
    } else {
      sendMessage(text);
    }
    setDraft('');
  }, [canSend, draft, editingItem, editMode, sendMessage]);

  const handleCancelEdit = useCallback(() => {
    setEditingItem(null);
    setDraft('');
  }, []);

  const handleEdit = useCallback((item: ChatUserItem) => {
    setEditingItem(item);
    setDraft(item.text);
    setEditMode(defaultEditMode);
    setActionMenu(null);
  }, [defaultEditMode]);

  const handleResend = useCallback(
    (item: ChatUserItem) => {
      setActionMenu(null);
      setEditingItem(null);
      setDraft('');
      sendMessage(item.text, { replaceFromMessageId: item.id, mode: defaultEditMode });
    },
    [sendMessage, defaultEditMode],
  );

  const handleRegenerate = useCallback(
    (assistant: ChatAssistantItem) => {
      setActionMenu(null);
      const turn = turns.find((candidate) => candidate.final?.id === assistant.id);
      if (turn?.user == null) return;
      sendMessage(turn.user.text, { replaceFromMessageId: turn.user.id, mode: defaultEditMode });
    },
    [turns, sendMessage, defaultEditMode],
  );

  const lastAssistantId = (() => {
    for (let i = turns.length - 1; i >= 0; i -= 1) {
      const final = turns[i]?.final;
      if (final) return final.id;
    }
    return null;
  })();

  // M8-01: while a turn is in flight, the Send button becomes a Stop
  // button (same slot in the composer, swapped by `busy`).
  const handleStop = useCallback(() => {
    stopTurn();
  }, [stopTurn]);

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
          data={turns}
          keyExtractor={(turn) => turn.id}
          renderItem={({ item: turn }) => (
            <ChatTurnRow
              turn={turn}
              isLastAssistant={turn.final != null && turn.final.id === lastAssistantId}
              menuDisabled={menuDisabled}
              branchPoint={
                turn.user != null
                  ? branches.find((point) => point.anchor_message_id === turn.user?.id)
                  : undefined
              }
              onSwitchBranch={(checkpointId) => {
                void switchBranch(checkpointId);
              }}
              onUserMenu={(user) => setActionMenu({ kind: 'user', item: user })}
              onAssistantMenu={(assistant) => setActionMenu({ kind: 'assistant', item: assistant })}
            />
          )}
          contentContainerStyle={styles.listContent}
          style={styles.list}
          // Collapsed-by-default activity means token growth does not
          // change list height, so this is no longer per-token. While a
          // turn is running the list stays pinned to the bottom so the
          // panel (and later the final bubble) stay in view.
          onContentSizeChange={() => {
            if (busy) listRef.current?.scrollToEnd({ animated: false });
          }}
          onLayout={() => listRef.current?.scrollToEnd({ animated: false })}
        />
        {pendingApproval !== null ? (
          <ApprovalCard pendingApproval={pendingApproval} onRespond={respondToApproval} />
        ) : null}
        {editingItem !== null ? (
          <View style={styles.editBanner} testID="chat-edit-banner">
            <View style={styles.editBannerMain}>
              <Text style={styles.editBannerText}>
                {editMode === 'fork'
                  ? 'Editing — sending keeps the old continuation as a branch'
                  : 'Editing — sending replaces everything after this message'}
              </Text>
              <View style={styles.editModeRow}>
                <Pressable
                  style={[styles.editModeChip, editMode === 'truncate' && styles.editModeChipSelected]}
                  onPress={() => setEditMode('truncate')}
                  accessibilityRole="button"
                  accessibilityLabel="Replace"
                  testID="chat-edit-mode-truncate"
                >
                  <Text
                    style={[
                      styles.editModeChipText,
                      editMode === 'truncate' && styles.editModeChipTextSelected,
                    ]}
                  >
                    Replace
                  </Text>
                </Pressable>
                <Pressable
                  style={[styles.editModeChip, editMode === 'fork' && styles.editModeChipSelected]}
                  onPress={() => setEditMode('fork')}
                  accessibilityRole="button"
                  accessibilityLabel="Branch"
                  testID="chat-edit-mode-fork"
                >
                  <Text
                    style={[styles.editModeChipText, editMode === 'fork' && styles.editModeChipTextSelected]}
                  >
                    Branch
                  </Text>
                </Pressable>
              </View>
            </View>
            <Pressable
              onPress={handleCancelEdit}
              accessibilityRole="button"
              accessibilityLabel="Cancel editing"
              testID="chat-edit-cancel"
            >
              <Text style={styles.editBannerCancel}>Cancel</Text>
            </Pressable>
          </View>
        ) : null}
        <View style={styles.composer}>
          <TextInput
            style={styles.input}
            value={draft}
            onChangeText={setDraft}
            onKeyPress={handleKeyPress}
            placeholder="Message…"
            placeholderTextColor={theme.textMuted}
            multiline
            editable={pendingApproval === null}
          />
          {busy ? (
            <Pressable
              style={styles.sendButton}
              onPress={handleStop}
              accessibilityRole="button"
              accessibilityLabel="Stop generating"
              testID="chat-stop"
            >
              <Ionicons name="square" size={16} color={theme.text} />
            </Pressable>
          ) : (
            <Pressable
              style={[styles.sendButton, !canSend && styles.sendButtonDisabled]}
              onPress={handleSend}
              disabled={!canSend}
              accessibilityRole="button"
              accessibilityLabel="Send message"
            >
              <Ionicons name="send" size={18} color={canSend ? theme.text : theme.textMuted} />
            </Pressable>
          )}
        </View>
        <MessageActionSheet
          menu={actionMenu}
          onClose={() => setActionMenu(null)}
          onEdit={handleEdit}
          onResend={handleResend}
          onRegenerate={handleRegenerate}
          onCopy={(text) => {
            void copyToClipboard(text);
            setActionMenu(null);
          }}
        />
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

interface ChatTurnRowProps {
  turn: ChatTurn;
  isLastAssistant: boolean;
  menuDisabled: boolean;
  branchPoint?: ThreadBranchPoint;
  onSwitchBranch: (checkpointId: string) => void;
  onUserMenu: (item: ChatUserItem) => void;
  onAssistantMenu: (item: ChatAssistantItem) => void;
}

function showActivityPanel(turn: ChatTurn): boolean {
  if (turn.status === 'running' || turn.status === 'cancelled' || turn.status === 'error') {
    return true;
  }
  return turn.activity.length > 0;
}

function ChatTurnRow({
  turn,
  isLastAssistant,
  menuDisabled,
  branchPoint,
  onSwitchBranch,
  onUserMenu,
  onAssistantMenu,
}: ChatTurnRowProps): ReactElement {
  const user = turn.user;
  const showPanel = showActivityPanel(turn);
  const showDurationCaption =
    !showPanel && turn.final != null && turn.durationMs != null && turn.status === 'completed';

  return (
    <View style={styles.turnBlock} testID="chat-turn">
      {user != null ? (
        <View style={styles.userColumn} testID="chat-item-user">
          <View style={[styles.bubbleRow, styles.bubbleRowRight]}>
            <MessageMenuAffordance
              testID="chat-item-user-menu"
              accessibilityLabel="Message actions"
              disabled={menuDisabled}
              onOpen={() => onUserMenu(user)}
            />
            <Pressable
              style={[styles.bubble, styles.userBubble]}
              onLongPress={menuDisabled ? undefined : () => onUserMenu(user)}
              delayLongPress={400}
              testID="chat-item-user-bubble"
            >
              <Text style={styles.bubbleText}>{user.text}</Text>
            </Pressable>
          </View>
          {branchPoint != null ? (
            <BranchSwitcher point={branchPoint} disabled={menuDisabled} onSwitch={onSwitchBranch} />
          ) : null}
        </View>
      ) : null}
      {showPanel ? (
        <TurnActivityPanel turn={turn}>
          {turn.activity.map((item) => (
            <ActivityItemRow key={item.id} item={item} />
          ))}
        </TurnActivityPanel>
      ) : null}
      {showDurationCaption ? (
        <Text style={styles.durationCaption} testID="turn-activity-caption">
          {formatDuration(turn.durationMs!)}
        </Text>
      ) : null}
      {turn.final != null ? (
        <View style={[styles.bubbleRow, styles.bubbleRowLeft]} testID="chat-item-assistant">
          <Pressable
            style={[styles.bubble, styles.assistantBubble]}
            onLongPress={isLastAssistant && !menuDisabled ? () => onAssistantMenu(turn.final!) : undefined}
            delayLongPress={400}
            testID="chat-item-assistant-bubble"
          >
            <Markdown>{turn.final.text}</Markdown>
          </Pressable>
          {isLastAssistant ? (
            <MessageMenuAffordance
              testID="chat-item-assistant-menu"
              accessibilityLabel="Regenerate actions"
              disabled={menuDisabled}
              onOpen={() => onAssistantMenu(turn.final!)}
            />
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

function ActivityItemRow({ item }: { item: ChatItem }): ReactElement | null {
  switch (item.kind) {
    case 'assistant':
      if (item.text === '') return null;
      return (
        <Text style={styles.activityPlainText} testID="turn-activity-text">
          {item.text}
        </Text>
      );
    case 'reasoning':
      if (item.text === '') return null;
      return (
        <Text style={styles.activityReasoningText} testID="turn-activity-reasoning">
          {item.text}
        </Text>
      );
    case 'tool':
      return <ToolItemCard item={item} />;
    case 'error':
      return (
        <View style={[styles.bubble, styles.errorBubble]} testID="chat-item-error">
          <Text style={styles.errorText}>{item.message}</Text>
        </View>
      );
    default:
      return null;
  }
}

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

/** Chip precedence mirrors `execChipInfo`'s own doc: `null` means "nothing
 * special to show here, fall back to the generic checkmark/x icon". */
function webSearchChipInfo(parsed: ParsedSearchResults): ExecChipInfo | null {
  if (parsed.isError) return { text: 'error', color: theme.danger };
  return { text: `${parsed.resultCount} result${parsed.resultCount === 1 ? '' : 's'}`, color: theme.textMuted };
}

/** Unlike `web_search`, a successful `web_fetch` gets no chip at all — the
 * page title is already folded into the collapsed header text itself (see
 * `webFetchHeaderText`), so a chip here would be redundant. Only the error
 * case needs a chip (there's nothing else in the header to signal it). */
function webFetchChipInfo(parsed: ParsedFetchResult): ExecChipInfo | null {
  return parsed.isError ? { text: 'error', color: theme.danger } : null;
}

/** Collapsed-header text for a `web_search` card: `args.query` (defensive
 * fallback to `item.name`, same reasoning as `firstLine`'s `command`
 * fallback — `args` isn't guaranteed to have the expected shape at the
 * type level even though the server always sends it). */
function webSearchHeaderText(item: ChatToolItem): string {
  return typeof item.args.query === 'string' ? item.args.query : item.name;
}

/** Collapsed-header text for a `web_fetch` card: `args.url`'s hostname,
 * plus (once a non-error result has arrived) the fetched page's title —
 * spec: "hostname of args.url + page title ... once done". While running
 * or on error, just the hostname (the error itself surfaces via
 * `webFetchChipInfo`'s chip, not duplicated into this text). */
function webFetchHeaderText(item: ChatToolItem, parsed: ParsedFetchResult | null): string {
  const hostname = typeof item.args.url === 'string' ? hostnameFromUrl(item.args.url) : item.name;
  if (parsed === null || parsed.isError || parsed.title === null || parsed.title.length === 0) return hostname;
  return `${hostname} — ${parsed.title}`;
}

const WEB_FETCH_TRUNCATED_MARKER = '[content truncated]';

/** Expanded detail for a `web_search` card — spec: "each result as title
 * (tappable, opens the URL externally — `Linking.openURL`; web: new tab) +
 * hostname + snippet." `Linking.openURL(url)` (single-arg call) is exactly
 * what gets the "web: new tab" behavior for free: `react-native-web`'s
 * `Linking.openURL` defaults its `target` param to `'_blank'` only when
 * called with exactly one argument (see its own source) — passing a second
 * arg explicitly would defeat that default, so this deliberately never
 * does. On native, `Linking.openURL` already opens externally regardless
 * of arg count. */
function WebSearchToolDetail({ item, parsed }: { item: ChatToolItem; parsed: ParsedSearchResults | null }): ReactElement {
  if (parsed === null) {
    // Still running — nothing to show yet beyond the query already visible
    // in the collapsed header, so just echo it here too rather than an
    // empty-looking expanded panel.
    return (
      <View style={styles.toolDetail}>
        <Text style={styles.toolDetailText}>{webSearchHeaderText(item)}</Text>
      </View>
    );
  }

  if (parsed.isError) {
    return (
      <View style={styles.toolDetail}>
        <Text style={styles.toolDetailText}>{parsed.errorMessage ?? 'Search failed.'}</Text>
      </View>
    );
  }

  if (parsed.results.length === 0) {
    return (
      <View style={styles.toolDetail}>
        <Text style={styles.toolDetailText}>No results found.</Text>
      </View>
    );
  }

  return (
    <View style={styles.toolDetail}>
      {parsed.results.map((result, index) => (
        <View key={`${index}-${result.url}`} style={[styles.searchResultRow, index === 0 && styles.searchResultRowFirst]}>
          <Pressable onPress={() => Linking.openURL(result.url)} accessibilityRole="link" accessibilityLabel={result.title}>
            <Text style={styles.searchResultTitle} numberOfLines={2} ellipsizeMode="tail">
              {result.title}
            </Text>
          </Pressable>
          <Text style={styles.searchResultHostname} numberOfLines={1} ellipsizeMode="tail">
            {hostnameFromUrl(result.url)}
          </Text>
          {result.snippet.length > 0 ? (
            <Text style={styles.searchResultSnippet} numberOfLines={3} ellipsizeMode="tail">
              {result.snippet}
            </Text>
          ) : null}
        </View>
      ))}
    </View>
  );
}

/** Expanded detail for a `web_fetch` card — spec: "full final URL
 * (tappable) + the extracted text in a scrollable block (max height ~40%
 * of screen, reuse the exec card's block style)." Reuses
 * `styles.execOutputScroll`/`getExecOutputMaxHeight` verbatim, per spec. */
function WebFetchToolDetail({ item, parsed }: { item: ChatToolItem; parsed: ParsedFetchResult | null }): ReactElement {
  const argsUrl = typeof item.args.url === 'string' ? item.args.url : '';

  if (parsed === null) {
    // Still running.
    return (
      <View style={styles.toolDetail}>
        <Text style={styles.toolDetailText}>{argsUrl}</Text>
      </View>
    );
  }

  if (parsed.isError) {
    return (
      <View style={styles.toolDetail}>
        {argsUrl.length > 0 ? (
          <Pressable onPress={() => Linking.openURL(argsUrl)} accessibilityRole="link">
            <Text style={[styles.toolDetailText, styles.fetchUrlLink]} numberOfLines={1} ellipsizeMode="tail">
              {argsUrl}
            </Text>
          </Pressable>
        ) : null}
        <Text style={styles.toolDetailText}>{parsed.errorMessage ?? 'Fetch failed.'}</Text>
      </View>
    );
  }

  // `parsed.url` (the `URL:` line, i.e. the real final/redirected URL) is
  // preferred over the request's own `args.url` per spec ("full final
  // URL") — `parsed.url` can only be `null` here if the preview was cut
  // before that line ever fully arrived (see `parseFetchResult`'s doc),
  // in which case falling back to the requested url is still the most
  // useful tappable link available.
  const finalUrl = parsed.url ?? argsUrl;

  return (
    <View style={styles.toolDetail}>
      {finalUrl.length > 0 ? (
        <Pressable onPress={() => Linking.openURL(finalUrl)} accessibilityRole="link">
          <Text style={[styles.toolDetailText, styles.fetchUrlLink]} numberOfLines={1} ellipsizeMode="tail">
            {finalUrl}
          </Text>
        </Pressable>
      ) : null}
      <ScrollView style={[styles.execOutputScroll, { maxHeight: getExecOutputMaxHeight() }]}>
        <Text style={styles.toolDetailText}>{parsed.text}</Text>
        {parsed.truncatedByTool ? <Text style={styles.execTruncatedText}>{WEB_FETCH_TRUNCATED_MARKER}</Text> : null}
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------------------
// M8-05: branch switcher
// ---------------------------------------------------------------------------

function BranchSwitcher({
  point,
  disabled,
  onSwitch,
}: {
  point: ThreadBranchPoint;
  disabled: boolean;
  onSwitch: (checkpointId: string) => void;
}): ReactElement {
  const total = point.branches.length;
  const current = point.active_index + 1;
  const go = (delta: number) => {
    if (disabled || total < 2) return;
    const nextIndex = (point.active_index + delta + total) % total;
    const next = point.branches[nextIndex];
    if (next && nextIndex !== point.active_index) onSwitch(next.checkpoint_id);
  };

  return (
    <View style={styles.branchSwitcher} testID="chat-branch-switcher">
      <Pressable
        onPress={() => go(-1)}
        disabled={disabled}
        accessibilityRole="button"
        accessibilityLabel="Previous branch"
        testID="chat-branch-prev"
      >
        <Text style={[styles.branchSwitcherText, disabled && styles.branchSwitcherDisabled]}>‹</Text>
      </Pressable>
      <Text style={styles.branchSwitcherText} testID="chat-branch-label">
        {`${current}/${total}`}
      </Text>
      <Pressable
        onPress={() => go(1)}
        disabled={disabled}
        accessibilityRole="button"
        accessibilityLabel="Next branch"
        testID="chat-branch-next"
      >
        <Text style={[styles.branchSwitcherText, disabled && styles.branchSwitcherDisabled]}>›</Text>
      </Pressable>
    </View>
  );
}

// ---------------------------------------------------------------------------
// M8-04: Edit / Resend / Regenerate
// ---------------------------------------------------------------------------

/** Web: hover "⋯" button (always in the DOM so Playwright can click it).
 * Native: the parent bubble's `onLongPress` is the affordance — this
 * control is hidden off-web. */
function MessageMenuAffordance({
  testID,
  accessibilityLabel,
  disabled,
  onOpen,
}: {
  testID: string;
  accessibilityLabel: string;
  disabled: boolean;
  onOpen: () => void;
}): ReactElement | null {
  if (Platform.OS !== 'web') return null;
  return (
    <Pressable
      style={[styles.messageMenuButton, disabled && styles.messageMenuButtonDisabled]}
      onPress={disabled ? undefined : onOpen}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      testID={testID}
    >
      <Ionicons name="ellipsis-horizontal" size={16} color={disabled ? theme.textMuted : theme.text} />
    </Pressable>
  );
}

interface MessageActionSheetProps {
  menu: { kind: 'user'; item: ChatUserItem } | { kind: 'assistant'; item: ChatAssistantItem } | null;
  onClose: () => void;
  onEdit: (item: ChatUserItem) => void;
  onResend: (item: ChatUserItem) => void;
  onRegenerate: (item: ChatAssistantItem) => void;
  onCopy: (text: string) => void;
}

/** Bottom sheet matching `FileActionSheet`'s custom-Modal convention. */
function MessageActionSheet({
  menu,
  onClose,
  onEdit,
  onResend,
  onRegenerate,
  onCopy,
}: MessageActionSheetProps): ReactElement | null {
  if (menu === null) return null;

  const copyAction = {
    key: 'copy',
    label: 'Copy',
    icon: 'copy-outline' as const,
    run: () => onCopy(menu.item.text),
  };

  const actions =
    menu.kind === 'user'
      ? [
          copyAction,
          { key: 'edit', label: 'Edit', icon: 'create-outline' as const, run: () => onEdit(menu.item) },
          { key: 'resend', label: 'Resend', icon: 'refresh-outline' as const, run: () => onResend(menu.item) },
        ]
      : [
          copyAction,
          {
            key: 'regenerate',
            label: 'Regenerate',
            icon: 'refresh-outline' as const,
            run: () => onRegenerate(menu.item),
          },
        ];

  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose} testID="chat-message-menu">
      <Pressable style={styles.messageMenuOverlay} onPress={onClose} accessibilityRole="button" accessibilityLabel="Close">
        <Pressable style={styles.messageMenuSheet} onPress={(event) => event.stopPropagation()}>
          {actions.map((action) => (
            <Pressable
              key={action.key}
              style={styles.messageMenuRow}
              onPress={action.run}
              accessibilityRole="button"
              accessibilityLabel={action.label}
              testID={`chat-message-action-${action.key}`}
            >
              <Ionicons name={action.icon} size={20} color={theme.text} />
              <Text style={styles.messageMenuLabel}>{action.label}</Text>
            </Pressable>
          ))}
        </Pressable>
      </Pressable>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// M8-03: ApprovalCard
// ---------------------------------------------------------------------------

/** First `n` lines of `text`, joined back with `\n` — used for the "first 20
 * lines" args preview spec (`write_file`'s `content` / `edit_file`'s
 * `new_string`). Appends a `… (N more lines)` marker when truncated, same
 * spirit as the exec/web_fetch cards' own `[output truncated]` markers. */
function firstNLines(text: string, n: number): string {
  const lines = text.split('\n');
  if (lines.length <= n) return text;
  return `${lines.slice(0, n).join('\n')}\n… (${lines.length - n} more lines)`;
}

/** Renders one pending action's `args` per the spec's per-type rules: exec
 * (`execute_code`) -> the command in monospace; file ops (`write_file`/
 * `edit_file`/`delete`) -> the path + first ~20 lines of content/edit.
 * Falls back to a raw JSON dump for anything else (defensive — the four
 * mutating tools are the only ones `interrupt_on` ever covers today, see
 * `app/agent/build.py`'s `MUTATING_TOOL_NAMES`). */
function ApprovalActionArgs({ action }: { action: PendingApprovalAction }): ReactElement {
  const args = action.args;
  if (action.category === 'exec') {
    const command = typeof args.command === 'string' ? args.command : JSON.stringify(args);
    return <Text style={[styles.toolDetailText, styles.approvalArgsText]}>{command}</Text>;
  }

  if (action.category === 'file') {
    const path = typeof args.file_path === 'string' ? args.file_path : (typeof args.path === 'string' ? args.path : '?');
    let bodyPreview: string | null = null;
    if (action.name === 'write_file' && typeof args.content === 'string') {
      bodyPreview = firstNLines(args.content, 20);
    } else if (action.name === 'edit_file' && typeof args.new_string === 'string') {
      const oldPreview = typeof args.old_string === 'string' ? firstNLines(args.old_string, 20) : '';
      bodyPreview = `- ${oldPreview}\n+ ${firstNLines(args.new_string, 20)}`;
    }
    return (
      <View style={styles.approvalArgsColumn}>
        <Text style={[styles.toolDetailText, styles.approvalArgsText]} numberOfLines={1} ellipsizeMode="middle">
          {path}
        </Text>
        {bodyPreview !== null ? (
          <Text style={[styles.toolDetailText, styles.approvalArgsText]}>{bodyPreview}</Text>
        ) : null}
      </View>
    );
  }

  return <Text style={[styles.toolDetailText, styles.approvalArgsText]}>{JSON.stringify(args)}</Text>;
}

interface ApprovalCardProps {
  pendingApproval: PendingApproval;
  onRespond: (decisions: ApprovalDecision[]) => void;
}

/**
 * `ApprovalCard` (M8-03) — one row per pending action, with per-row
 * Approve/Reject buttons plus an "Approve all" button when there's more
 * than one action. All buttons disable immediately after ANY response is
 * sent (`responded`, local state) to avoid a double-submit race — the
 * parent's `pendingApproval` itself also flips to `null` right after
 * (`useChat.respondToApproval`), but that happens one render later, and
 * this card would otherwise still be mounted (and tappable) for that one
 * frame without its own guard.
 */
function ApprovalCard({ pendingApproval, onRespond }: ApprovalCardProps): ReactElement {
  const [responded, setResponded] = useState(false);
  // Per-row picks for the multi-action case: the WS contract requires one
  // decision per pending `tool_call_id` in a single `approval_response`,
  // so a lone Approve/Reject tap on one of N rows only records that row
  // until every row has a pick (then we send). A single-action card
  // still sends immediately — see `respondOne`.
  const [rowDecisions, setRowDecisions] = useState<
    Partial<Record<string, ApprovalDecision['decision']>>
  >({});

  const respond = useCallback(
    (decisions: ApprovalDecision[]) => {
      if (responded) return;
      setResponded(true);
      onRespond(decisions);
    },
    [responded, onRespond],
  );

  const respondOne = useCallback(
    (toolCallId: string, decision: ApprovalDecision['decision']) => {
      if (pendingApproval.actions.length === 1) {
        respond([{ tool_call_id: toolCallId, decision }]);
        return;
      }
      const next = { ...rowDecisions, [toolCallId]: decision };
      setRowDecisions(next);
      if (pendingApproval.actions.every((action) => next[action.toolCallId])) {
        respond(
          pendingApproval.actions.map((action) => ({
            tool_call_id: action.toolCallId,
            decision: next[action.toolCallId] as ApprovalDecision['decision'],
          })),
        );
      }
    },
    [respond, pendingApproval.actions, rowDecisions],
  );

  const approveAll = useCallback(() => {
    respond(pendingApproval.actions.map((action) => ({ tool_call_id: action.toolCallId, decision: 'approve' })));
  }, [respond, pendingApproval.actions]);

  return (
    <View style={styles.approvalCard} testID="approval-card">
      <Text style={styles.approvalCardTitle}>
        {pendingApproval.actions.length > 1 ? 'These actions need your approval' : 'This action needs your approval'}
      </Text>
      {pendingApproval.actions.map((action) => (
        <View key={action.toolCallId} style={styles.approvalRow} testID="approval-row">
          <View style={styles.approvalRowHeader}>
            <Ionicons name={CATEGORY_ICON[action.category]} size={16} color={theme.textMuted} />
            <Text style={styles.toolName}>{action.name}</Text>
          </View>
          <ApprovalActionArgs action={action} />
          <Text style={styles.approvalDescription}>{action.description}</Text>
          <View style={styles.approvalButtonRow}>
            <Pressable
              style={[
                styles.approvalButton,
                styles.approvalRejectButton,
                rowDecisions[action.toolCallId] === 'reject' && styles.approvalRejectButtonSelected,
                responded && styles.approvalButtonDisabled,
              ]}
              onPress={() => respondOne(action.toolCallId, 'reject')}
              disabled={responded}
              accessibilityRole="button"
              accessibilityLabel={`Reject ${action.name}`}
            >
              <Text style={styles.approvalRejectButtonText}>Reject</Text>
            </Pressable>
            <Pressable
              style={[
                styles.approvalButton,
                styles.approvalApproveButton,
                rowDecisions[action.toolCallId] === 'approve' && styles.approvalApproveButtonSelected,
                responded && styles.approvalButtonDisabled,
              ]}
              onPress={() => respondOne(action.toolCallId, 'approve')}
              disabled={responded}
              accessibilityRole="button"
              accessibilityLabel={`Approve ${action.name}`}
            >
              <Text style={styles.approvalApproveButtonText}>Approve</Text>
            </Pressable>
          </View>
        </View>
      ))}
      {pendingApproval.actions.length > 1 ? (
        <Pressable
          style={[styles.approvalButton, styles.approvalApproveAllButton, responded && styles.approvalButtonDisabled]}
          onPress={approveAll}
          disabled={responded}
          accessibilityRole="button"
          accessibilityLabel="Approve all"
        >
          <Text style={styles.approvalApproveButtonText}>Approve all</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

function ToolItemCard({ item }: { item: ChatToolItem }): ReactElement {
  const [expanded, setExpanded] = useState(false);
  const isExec = item.category === 'exec';
  const isWebSearch = item.category === 'web' && item.name === 'web_search';
  const isWebFetch = item.category === 'web' && item.name === 'web_fetch';

  const execParsed = isExec && item.resultPreview !== undefined ? parseExecResult(item.resultPreview) : null;
  const searchParsed = isWebSearch && item.resultPreview !== undefined ? parseSearchResults(item.resultPreview) : null;
  const fetchParsed = isWebFetch && item.resultPreview !== undefined ? parseFetchResult(item.resultPreview) : null;

  const chip =
    execParsed !== null
      ? execChipInfo(execParsed)
      : searchParsed !== null
        ? webSearchChipInfo(searchParsed)
        : fetchParsed !== null
          ? webFetchChipInfo(fetchParsed)
          : null;

  const headerIcon: keyof typeof Ionicons.glyphMap = isWebSearch ? 'search-outline' : CATEGORY_ICON[item.category];

  return (
    <View style={styles.toolCard} testID="chat-item-tool">
      <Pressable style={styles.toolHeader} onPress={() => setExpanded((prev) => !prev)} testID="chat-item-tool-header">
        <Ionicons name={headerIcon} size={16} color={theme.textMuted} />
        {isExec ? (
          <Text style={styles.toolName} numberOfLines={1} ellipsizeMode="tail">
            {firstLine(item.args.command)}
          </Text>
        ) : isWebSearch ? (
          <Text style={styles.toolName} numberOfLines={1} ellipsizeMode="tail">
            {webSearchHeaderText(item)}
          </Text>
        ) : isWebFetch ? (
          <Text style={styles.toolName} numberOfLines={1} ellipsizeMode="tail">
            {webFetchHeaderText(item, fetchParsed)}
          </Text>
        ) : (
          <Text style={styles.toolName}>{item.name}</Text>
        )}
        {item.status === 'running' ? (
          <ActivityIndicator size="small" color={theme.accent} style={styles.toolStatusIcon} />
        ) : item.status === 'rejected' ? (
          // M8-03: mirrors the exec-chip visual convention exactly (small
          // pill, colored border+text) for a locally-synthesized rejected
          // action — see `useChat.respondToApproval`'s doc for why this
          // item has no real `tool_start`/`tool_end` frame behind it.
          <View style={[styles.execChip, { borderColor: theme.danger }]} testID="chat-item-tool-rejected-chip">
            <Text style={[styles.execChipText, { color: theme.danger }]}>rejected</Text>
          </View>
        ) : chip !== null ? (
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
        ) : isWebSearch ? (
          <WebSearchToolDetail item={item} parsed={searchParsed} />
        ) : isWebFetch ? (
          <WebFetchToolDetail item={item} parsed={fetchParsed} />
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
  turnBlock: {
    gap: 8,
  },
  durationCaption: {
    color: theme.textMuted,
    fontSize: 11,
    alignSelf: 'flex-start',
    marginLeft: 4,
  },
  activityPlainText: {
    color: theme.textMuted,
    fontSize: 13,
    lineHeight: 18,
  },
  activityReasoningText: {
    color: theme.textMuted,
    fontSize: 13,
    lineHeight: 18,
    fontStyle: 'italic',
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
  stoppedCaption: {
    color: theme.textMuted,
    fontSize: 11,
    fontStyle: 'italic',
    marginTop: 4,
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
  searchResultRow: {
    gap: 2,
    paddingVertical: 4,
    borderTopWidth: 1,
    borderTopColor: theme.border,
  },
  searchResultRowFirst: {
    borderTopWidth: 0,
    paddingTop: 0,
  },
  searchResultTitle: {
    color: theme.accent,
    fontSize: 13,
    fontWeight: '600',
  },
  searchResultHostname: {
    color: theme.textMuted,
    fontSize: 11,
  },
  searchResultSnippet: {
    color: theme.text,
    fontSize: 12,
    lineHeight: 16,
  },
  fetchUrlLink: {
    color: theme.accent,
  },
  messageMenuButton: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
    alignSelf: 'center',
  },
  messageMenuButtonDisabled: {
    opacity: 0.4,
  },
  messageMenuOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    justifyContent: 'flex-end',
  },
  messageMenuSheet: {
    backgroundColor: theme.surface,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    borderWidth: 1,
    borderColor: theme.border,
    paddingHorizontal: 8,
    paddingTop: 12,
    paddingBottom: 24,
  },
  messageMenuRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
    paddingHorizontal: 12,
    paddingVertical: 14,
  },
  messageMenuLabel: {
    color: theme.text,
    fontSize: 16,
  },
  editBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    marginHorizontal: 12,
    marginBottom: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: theme.accent,
    backgroundColor: theme.surface,
  },
  editBannerMain: {
    flex: 1,
    gap: 8,
  },
  editBannerText: {
    color: theme.text,
    fontSize: 13,
  },
  editModeRow: {
    flexDirection: 'row',
    gap: 6,
  },
  editModeChip: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: theme.border,
    backgroundColor: theme.bg,
  },
  editModeChipSelected: {
    borderColor: theme.accent,
    backgroundColor: theme.accent,
  },
  editModeChipText: {
    color: theme.textMuted,
    fontSize: 12,
    fontWeight: '600',
  },
  editModeChipTextSelected: {
    color: theme.text,
  },
  editBannerCancel: {
    color: theme.accent,
    fontSize: 13,
    fontWeight: '600',
  },
  userColumn: {
    alignItems: 'flex-end',
    gap: 4,
  },
  branchSwitcher: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 4,
  },
  branchSwitcherText: {
    color: theme.textMuted,
    fontSize: 12,
    fontWeight: '600',
  },
  branchSwitcherDisabled: {
    opacity: 0.4,
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
  approvalCard: {
    marginHorizontal: 12,
    marginBottom: 8,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: theme.accent,
    backgroundColor: theme.surface,
    padding: 10,
    gap: 8,
  },
  approvalCardTitle: {
    color: theme.text,
    fontSize: 13,
    fontWeight: '600',
  },
  approvalRow: {
    gap: 4,
    borderTopWidth: 1,
    borderTopColor: theme.border,
    paddingTop: 8,
  },
  approvalRowHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  approvalArgsColumn: {
    gap: 2,
  },
  approvalArgsText: {
    color: theme.text,
  },
  approvalDescription: {
    color: theme.textMuted,
    fontSize: 12,
  },
  approvalButtonRow: {
    flexDirection: 'row',
    gap: 8,
    marginTop: 4,
  },
  approvalButton: {
    flex: 1,
    minHeight: 40,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  approvalButtonDisabled: {
    opacity: 0.5,
  },
  approvalRejectButton: {
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.danger,
  },
  approvalRejectButtonSelected: {
    borderWidth: 2,
  },
  approvalApproveButtonSelected: {
    borderWidth: 2,
    borderColor: theme.text,
  },
  approvalRejectButtonText: {
    color: theme.danger,
    fontSize: 14,
    fontWeight: '600',
  },
  approvalApproveButton: {
    backgroundColor: theme.accent,
  },
  approvalApproveAllButton: {
    backgroundColor: theme.accent,
    marginTop: 4,
    minHeight: 44,
  },
  approvalApproveButtonText: {
    color: theme.text,
    fontSize: 14,
    fontWeight: '600',
  },
});
