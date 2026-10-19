import { useState } from 'react';
import { ScrollView, Text, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { runAction, useMembers, useQuery, useSpace } from '@homeai/sdk';
import { Button, IconButton, List, ListRow, Screen, TextField, useTheme, type RowAction } from '@homeai/ui';
import { CARD_COLUMNS, COLUMN_COLUMNS, columnTitle, dueLabel, today, type Board, type Card, type Column } from '../../../lib/kanban';

const WIDTH = 300;

export default function BoardScreen() {
  const t = useTheme();
  const router = useRouter();
  const members = useMembers();
  const canEdit = useSpace()?.role !== 'viewer';
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data: boards, loading } = useQuery<Board>('SELECT id, name FROM boards WHERE id = ?', [Number(id)]);
  const { data: columns } = useQuery<Column>(`SELECT ${COLUMN_COLUMNS} FROM columns WHERE board_id = ? ORDER BY position, id`, [Number(id)]);
  const { data: cards } = useQuery<Card>(
    `SELECT ${CARD_COLUMNS} FROM cards WHERE column_id IN (SELECT id FROM columns WHERE board_id = ?) ORDER BY position, id`,
    [Number(id)],
  );
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const board = boards?.[0];

  if (!board) {
    return (
      <Screen>
        <Text testID="board-missing">{loading ? 'Loading…' : 'This board was deleted.'}</Text>
      </Screen>
    );
  }
  const cols = columns ?? [];
  const now = today();
  const who = (uid: string | null) => members.find((m) => m.id === uid)?.name;

  async function add(column: Column) {
    const title = (drafts[column.id] ?? '').trim();
    if (!title) return;
    await runAction('addCard', { column_id: column.id, title });
    setDrafts((d) => ({ ...d, [column.id]: '' }));
  }

  // Swipe a card (or long-press it) for: back a column, on a column, delete.
  const actionsFor = (card: Card, i: number) => {
    const to = (c: Column) => () => void runAction('moveCard', { card_id: card.id, column_id: c.id, position: 1_000_000 });
    const actions: RowAction[] = [];
    if (i > 0) actions.push({ label: cols[i - 1].name, icon: 'arrow-back-outline', color: t.colors.inactive, onPress: to(cols[i - 1]) });
    if (i < cols.length - 1) actions.push({ label: cols[i + 1].name, icon: 'arrow-forward-outline', color: t.colors.tint, onPress: to(cols[i + 1]) });
    actions.push({ label: 'Delete', icon: 'trash-outline', onPress: () => void runAction('deleteCard', { card_id: card.id }) });
    return actions;
  };

  return (
    <Screen padded={false}>
      <Stack.Screen
        options={{
          title: board.name,
          headerRight: () =>
            canEdit ? <IconButton icon="options-outline" accessibilityLabel="Columns" onPress={() => router.push({ pathname: '/board/[id]/columns', params: { id: String(board.id) } })} testID="board-columns" /> : null,
        }}
      />
      <ScrollView horizontal contentContainerStyle={{ padding: t.spacing.lg, gap: t.spacing.lg, alignItems: 'flex-start' }}>
        {cols.map((c, i) => {
          const mine = (cards ?? []).filter((card) => card.column_id === c.id);
          const over = c.wip_limit !== null && mine.length > c.wip_limit;
          return (
            <View key={c.id} style={{ width: WIDTH, gap: t.spacing.sm }} testID={`column-${c.id}`}>
              <Text style={{ color: over ? t.colors.danger : t.colors.textMuted, fontSize: t.font.small, fontWeight: '600', textTransform: 'uppercase' }} testID={`column-${c.id}-title`}>
                {columnTitle(c, mine.length)}
                {over ? ' · over the limit' : ''}
              </Text>
              <List
                data={mine}
                keyExtractor={(card) => card.id}
                renderItem={(card) => (
                  <ListRow
                    title={card.title}
                    subtitle={[card.due ? dueLabel(card.due) : null, who(card.assignee)].filter(Boolean).join(' · ') || undefined}
                    icon={card.due && card.due < now && i < cols.length - 1 ? 'alert-circle-outline' : undefined}
                    iconColor={t.colors.danger}
                    onPress={() => router.push({ pathname: '/card/[id]', params: { id: String(card.id) } })}
                    actions={canEdit ? actionsFor(card, i) : undefined}
                    testID={`card-${card.id}`}
                  />
                )}
              />
              {canEdit ? (
                <View style={{ gap: t.spacing.xs }}>
                  <TextField value={drafts[c.id] ?? ''} onChangeText={(text) => setDrafts((d) => ({ ...d, [c.id]: text }))} placeholder="Add a card" onSubmitEditing={() => void add(c)} testID={`add-${c.id}`} />
                  {drafts[c.id]?.trim() ? <Button title="Add" variant="secondary" onPress={() => void add(c)} testID={`add-${c.id}-save`} /> : null}
                </View>
              ) : null}
            </View>
          );
        })}
      </ScrollView>
      {canEdit && (cards ?? []).length ? (
        <Text style={{ color: t.colors.textMuted, fontSize: t.font.small, paddingHorizontal: t.spacing.lg }}>Swipe a card left (or long-press it) to move it along or delete it.</Text>
      ) : null}
    </Screen>
  );
}
