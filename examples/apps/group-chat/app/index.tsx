import { useState } from 'react';
import { ActivityIndicator, FlatList, Text, TextInput, View } from 'react-native';
import { useDatabase, useMembers, useQuery, useSpace, useUser } from '@homeai/sdk';
import { Button, EmptyState, IconButton, useTheme } from '@homeai/ui';
import { MessageRow } from '../components/MessageRow';
import { MAX_CHARS, MESSAGE_COLUMNS, PAGE, layout, type Message } from '../lib/chat';

export default function ChatScreen() {
  const t = useTheme();
  const db = useDatabase();
  const me = useUser();
  const members = useMembers();
  const canPost = useSpace()?.role !== 'viewer';
  // Newest first: the list is inverted, so they sit at the bottom. Scrolling up raises the limit.
  const [limit, setLimit] = useState(PAGE);
  const { data, error } = useQuery<Message>(`SELECT ${MESSAGE_COLUMNS} FROM messages ORDER BY id DESC LIMIT ?`, [limit]);
  const { data: counts } = useQuery<{ n: number }>('SELECT count(*) AS n FROM messages');
  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState<Message | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const messages = data ?? [];
  const total = counts?.[0]?.n ?? 0;
  const rows = layout(messages);
  const name = (id: string) => (id === me?.id ? 'you' : (members.find((m) => m.id === id)?.name ?? 'Someone'));
  const chosen = messages.find((m) => m.id === selected && !m.deleted && m._created_by === me?.id);

  async function send() {
    const body = draft.trim();
    if (!body) return;
    setSending(true);
    setProblem(null);
    try {
      if (editing) await db.runAsync('UPDATE messages SET body = ?, edited = 1 WHERE id = ? AND deleted = 0', [body, editing.id]);
      else await db.runAsync('INSERT INTO messages (body) VALUES (?)', [body]);
      setDraft('');
      setEditing(null);
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setSending(false);
    }
  }

  function startEdit(m: Message) {
    setEditing(m);
    setDraft(m.body);
    setSelected(null);
  }

  function cancelEdit() {
    setEditing(null);
    setDraft('');
  }

  async function remove(m: Message) {
    setSelected(null);
    if (editing?.id === m.id) cancelEdit();
    try {
      await db.runAsync("UPDATE messages SET body = '', deleted = 1 WHERE id = ?", [m.id]);
    } catch (e) {
      setProblem((e as Error).message);
    }
  }

  return (
    <View style={{ flex: 1, backgroundColor: t.colors.background }}>
      {error ? (
        <Text role="alert" style={{ color: t.colors.danger, padding: t.spacing.md }}>
          {error.message}
        </Text>
      ) : null}
      {data && !messages.length ? (
        <View style={{ flex: 1, justifyContent: 'center' }}>
          <EmptyState icon="chatbubbles-outline" title="No messages yet" message={canPost ? 'Say hello to everyone in the space.' : undefined} testID="chat-empty" />
        </View>
      ) : (
        <FlatList
          inverted
          data={messages}
          keyExtractor={(m) => String(m.id)}
          renderItem={({ item, index }) => {
            const mine = item._created_by === me?.id;
            return (
              <MessageRow
                message={item}
                mine={mine}
                newDay={rows[index]?.newDay ?? false}
                showSender={rows[index]?.showSender ?? false}
                senderName={name(item._created_by)}
                changedBy={item._updated_by !== item._created_by ? name(item._updated_by) : null}
                selected={selected === item.id}
                onPress={mine && canPost && !item.deleted ? () => setSelected((s) => (s === item.id ? null : item.id)) : undefined}
              />
            );
          }}
          onEndReached={() => messages.length < total && setLimit((n) => n + PAGE)}
          onEndReachedThreshold={0.3}
          ListFooterComponent={
            messages.length < total ? (
              <ActivityIndicator style={{ margin: t.spacing.md }} color={t.colors.textMuted} />
            ) : (
              <Text style={{ alignSelf: 'center', color: t.colors.textMuted, fontSize: t.font.small, margin: t.spacing.md }} testID="chat-start">
                The start of the chat
              </Text>
            )
          }
          contentContainerStyle={{ paddingHorizontal: t.spacing.md, paddingVertical: t.spacing.sm }}
          keyboardShouldPersistTaps="handled"
          testID="messages"
        />
      )}
      {chosen ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: t.spacing.sm, paddingHorizontal: t.spacing.md, borderTopWidth: 1, borderColor: t.colors.border, backgroundColor: t.colors.surface }} testID="message-actions">
          <Text style={{ flex: 1, color: t.colors.textMuted }}>Your message</Text>
          <Button title="Edit" variant="plain" icon="create-outline" onPress={() => startEdit(chosen)} testID="message-edit" />
          <Button title="Delete" variant="plain" icon="trash-outline" onPress={() => void remove(chosen)} testID="message-delete" />
          <IconButton icon="close-outline" accessibilityLabel="Close" onPress={() => setSelected(null)} testID="message-actions-close" />
        </View>
      ) : null}
      {canPost ? (
        <View style={{ gap: t.spacing.xs, padding: t.spacing.sm, borderTopWidth: 1, borderColor: t.colors.border, backgroundColor: t.colors.surface }}>
          {editing ? (
            <View style={{ flexDirection: 'row', alignItems: 'center' }}>
              <Text style={{ flex: 1, color: t.colors.textMuted }} testID="editing-banner">
                Editing your message
              </Text>
              <Button title="Cancel" variant="plain" onPress={cancelEdit} testID="edit-cancel" />
            </View>
          ) : null}
          <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: t.spacing.sm }}>
            <TextInput
              value={draft}
              onChangeText={setDraft}
              placeholder="Message"
              placeholderTextColor={t.colors.textMuted}
              multiline
              maxLength={MAX_CHARS}
              aria-label="Message"
              style={{
                flex: 1,
                maxHeight: 140,
                borderWidth: 1,
                borderColor: t.colors.border,
                borderRadius: t.radius.lg,
                paddingHorizontal: t.spacing.md,
                paddingVertical: t.spacing.sm,
                fontSize: t.font.body,
                color: t.colors.text,
                backgroundColor: t.colors.background,
              }}
              testID="composer"
            />
            <IconButton
              icon={editing ? 'checkmark-circle-outline' : 'send-outline'}
              accessibilityLabel={editing ? 'Save' : 'Send'}
              size={26}
              onPress={() => void send()}
              disabled={!draft.trim() || sending}
              testID="send"
            />
          </View>
          {problem ? (
            <Text role="alert" style={{ color: t.colors.danger, fontSize: t.font.small }} testID="chat-problem">
              {problem}
            </Text>
          ) : null}
        </View>
      ) : (
        <Text style={{ textAlign: 'center', color: t.colors.textMuted, padding: t.spacing.md, borderTopWidth: 1, borderColor: t.colors.border }} testID="view-only">
          View only: you can read the chat but not post.
        </Text>
      )}
    </View>
  );
}
