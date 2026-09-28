import Ionicons from '@expo/vector-icons/Ionicons';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { ActionButton, ErrorText } from '@/components/SettingsUI';
import { TurnActivityPanel } from '@/components/TurnActivityPanel';
import {
  CONTEXT_FILES,
  displayUserPrompt,
  loadAppContext,
  messageHasAppContext,
  seedUserMessage,
  type AppContext,
} from '@/lib/appAgent';
import type { Space } from '@/lib/platform';
import { theme } from '@/lib/theme';
import { createThread } from '@/lib/threads';
import {
  useChat,
  type ChatTurn,
  type ChatUserItem,
  type PendingApproval,
} from '@/lib/useChat';

const threadsByInstance = new Map<string, string>();

/**
 * Side panel on the app runner (M13-04): a regular chat thread pre-seeded
 * with this instance's app.json, AGENT.md, schema.sql, id and space.
 * `askAgent(prompt)` lands here as `initialPrompt`.
 */
export function AppAgentPanel({
  instanceId,
  space,
  appId,
  appName,
  initialPrompt,
  promptSeq = 0,
  onClose,
}: {
  instanceId: string;
  space: Space;
  appId: string;
  appName: string;
  initialPrompt?: string | null;
  /** Bumps on every `askAgent` so a repeated prompt is still a new turn. */
  promptSeq?: number;
  onClose: () => void;
}) {
  const [ctx, setCtx] = useState<AppContext | null>(null);
  const [threadId, setThreadId] = useState<string | null>(() => threadsByInstance.get(instanceId) ?? null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    loadAppContext(instanceId, space, appId, appName).then(
      (next) => !cancelled && setCtx(next),
      () => !cancelled && setError("Couldn't load this app's files."),
    );
    return () => {
      cancelled = true;
    };
  }, [instanceId, space, appId, appName]);

  useEffect(() => {
    if (threadId) {
      threadsByInstance.set(instanceId, threadId);
      return;
    }
    let cancelled = false;
    createThread(`Ask: ${appName}`).then(
      (thread) => {
        if (cancelled) return;
        threadsByInstance.set(instanceId, thread.id);
        setThreadId(thread.id);
      },
      () => !cancelled && setError("Couldn't start a chat."),
    );
    return () => {
      cancelled = true;
    };
  }, [instanceId, appName, threadId]);

  return (
    <View style={styles.panel} testID="app-agent-panel" accessibilityLabel="Ask the agent">
      <View style={styles.header}>
        <Text style={styles.title} numberOfLines={1}>
          Ask the agent
        </Text>
        <Pressable onPress={onClose} accessibilityRole="button" accessibilityLabel="Close" testID="app-agent-close" style={styles.iconButton}>
          <Ionicons name="close" size={22} color={theme.text} />
        </Pressable>
      </View>
      {error ? <ErrorText testID="app-agent-error">{error}</ErrorText> : null}
      {ctx ? <ContextCard ctx={ctx} /> : <ActivityIndicator color={theme.accent} style={styles.spinner} />}
      {ctx && threadId ? (
        <AgentChat
          threadId={threadId}
          ctx={ctx}
          initialPrompt={initialPrompt ?? null}
          promptSeq={promptSeq}
        />
      ) : (
        <View style={styles.flex} />
      )}
    </View>
  );
}

function ContextCard({ ctx }: { ctx: AppContext }) {
  return (
    <View style={styles.context} testID="app-agent-context">
      <Text style={styles.contextLine} testID="app-agent-instance">
        Instance {ctx.instanceId}
      </Text>
      <Text style={styles.contextLine} testID="app-agent-space">
        Space {ctx.space.slug} ({ctx.space.name})
      </Text>
      {CONTEXT_FILES.map((name) => (
        <Text key={name} style={styles.contextFile} testID={`app-agent-file-${name}`} numberOfLines={3}>
          {name}
          {ctx.files[name] ? `\n${ctx.files[name]}` : ' (unavailable)'}
        </Text>
      ))}
    </View>
  );
}

function AgentChat({
  threadId,
  ctx,
  initialPrompt,
  promptSeq,
}: {
  threadId: string;
  ctx: AppContext;
  initialPrompt: string | null;
  promptSeq: number;
}) {
  const { turns, sendMessage, busy, hydrationState, pendingApproval, respondToApproval } = useChat(threadId);
  const [draft, setDraft] = useState('');
  const seeded = useRef(false);
  const queued = useRef<string[]>([]);
  const consumedSeq = useRef<number | null>(null);

  useEffect(() => {
    if (hydrationState !== 'done') return;
    if (turns.some((turn) => turn.user && messageHasAppContext(turn.user.text))) seeded.current = true;
  }, [hydrationState, turns]);

  const send = useCallback(
    (text: string) => {
      const prompt = text.trim();
      if (!prompt) return;
      const wire = seeded.current ? prompt : seedUserMessage(ctx, prompt);
      seeded.current = true;
      sendMessage(wire);
    },
    [ctx, sendMessage],
  );

  useEffect(() => {
    const prompt = initialPrompt?.trim() ?? '';
    if (prompt && consumedSeq.current !== promptSeq) {
      queued.current.push(prompt);
      consumedSeq.current = promptSeq;
    }
    if (hydrationState !== 'done' || busy || pendingApproval) return;
    const next = queued.current.shift();
    if (next) send(next);
  }, [busy, hydrationState, initialPrompt, pendingApproval, promptSeq, send]);

  const canSend = !busy && pendingApproval === null && hydrationState === 'done' && draft.trim().length > 0;

  return (
    <View style={styles.flex}>
      <ScrollView style={styles.flex} contentContainerStyle={styles.transcript} testID="app-agent-transcript">
        {turns.map((turn) => (
          <TurnBlock key={turn.id} turn={turn} />
        ))}
      </ScrollView>
      {pendingApproval ? <PanelApproval pending={pendingApproval} onRespond={respondToApproval} /> : null}
      <View style={styles.composer}>
        <TextInput
          style={styles.input}
          value={draft}
          onChangeText={setDraft}
          placeholder="Ask about this app"
          placeholderTextColor={theme.textMuted}
          multiline
          testID="app-agent-composer"
          editable={!busy && pendingApproval === null}
        />
        <Pressable
          onPress={() => {
            send(draft);
            setDraft('');
          }}
          disabled={!canSend}
          accessibilityRole="button"
          accessibilityLabel="Send"
          testID="app-agent-send"
          style={[styles.send, !canSend && styles.sendDisabled]}
        >
          <Ionicons name="send" size={18} color={canSend ? theme.bg : theme.textMuted} />
        </Pressable>
      </View>
    </View>
  );
}

function TurnBlock({ turn }: { turn: ChatTurn }) {
  return (
    <View style={styles.turn}>
      {turn.user ? <UserBubble item={turn.user} /> : null}
      {turn.activity.length > 0 ? (
        <TurnActivityPanel turn={turn}>
          {turn.activity.map((item) => (
            <Text key={item.id} style={styles.activityLine}>
              {item.kind === 'tool' ? `${item.name} (${item.status})` : item.kind === 'assistant' ? item.text : item.kind}
            </Text>
          ))}
        </TurnActivityPanel>
      ) : null}
      {turn.final?.kind === 'assistant' ? <Text style={styles.assistant}>{turn.final.text}</Text> : null}
    </View>
  );
}

function UserBubble({ item }: { item: ChatUserItem }) {
  return (
    <Text style={styles.user} testID="app-agent-user-text">
      {displayUserPrompt(item.text)}
    </Text>
  );
}

function PanelApproval({
  pending,
  onRespond,
}: {
  pending: PendingApproval;
  onRespond: (decisions: { tool_call_id: string; decision: 'approve' | 'reject' }[]) => void;
}) {
  const decide = (decision: 'approve' | 'reject') =>
    onRespond(pending.actions.map((action) => ({ tool_call_id: action.toolCallId, decision })));
  return (
    <View style={styles.approval} testID="app-agent-approval">
      <Text style={styles.approvalText}>The agent wants to change this app&apos;s data.</Text>
      {pending.actions.map((action) => (
        <Text key={action.toolCallId} style={styles.activityLine}>
          {action.description || action.name}
        </Text>
      ))}
      <View style={styles.approvalRow}>
        <ActionButton label="Approve" variant="primary" onPress={() => decide('approve')} testID="app-agent-approve" />
        <ActionButton label="Reject" onPress={() => decide('reject')} testID="app-agent-reject" />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  panel: {
    width: 360,
    maxWidth: '100%',
    flex: 1,
    backgroundColor: theme.surface,
    borderLeftWidth: 1,
    borderLeftColor: theme.border,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: theme.border,
  },
  title: { color: theme.text, fontSize: 16, fontWeight: '600', flex: 1 },
  iconButton: { padding: 4 },
  spinner: { margin: 16 },
  context: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    gap: 4,
    borderBottomWidth: 1,
    borderBottomColor: theme.border,
    maxHeight: 180,
  },
  contextLine: { color: theme.textMuted, fontSize: 12 },
  contextFile: { color: theme.text, fontSize: 11, marginTop: 4 },
  flex: { flex: 1 },
  transcript: { padding: 12, gap: 12 },
  turn: { gap: 8 },
  user: {
    alignSelf: 'flex-end',
    backgroundColor: theme.accent,
    color: theme.bg,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 10,
    overflow: 'hidden',
    maxWidth: '90%',
  },
  assistant: { color: theme.text, fontSize: 14 },
  activityLine: { color: theme.textMuted, fontSize: 12 },
  composer: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 8,
    padding: 8,
    borderTopWidth: 1,
    borderTopColor: theme.border,
  },
  input: {
    flex: 1,
    minHeight: 40,
    maxHeight: 100,
    color: theme.text,
    backgroundColor: theme.bg,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
  },
  send: {
    backgroundColor: theme.accent,
    borderRadius: 8,
    padding: 10,
  },
  sendDisabled: { backgroundColor: theme.border },
  approval: {
    padding: 12,
    gap: 8,
    borderTopWidth: 1,
    borderTopColor: theme.danger,
  },
  approvalText: { color: theme.text, fontSize: 13 },
  approvalRow: { flexDirection: 'row', gap: 8 },
});
