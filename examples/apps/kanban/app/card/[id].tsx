import { useState } from 'react';
import { Text, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { runAction, useDatabase, useMembers, useQuery, useSpace } from '@homeai/sdk';
import { Button, DateField, Screen, Select, TextField, useTheme } from '@homeai/ui';
import { CARD_COLUMNS, COLUMN_COLUMNS, type Card, type Column } from '../../lib/kanban';

export default function CardScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Card>(`SELECT ${CARD_COLUMNS} FROM cards WHERE id = ?`, [Number(id)]);
  const card = data?.[0];
  if (!card) {
    return (
      <Screen>
        <Text testID="card-missing">{loading ? 'Loading…' : 'This card was deleted.'}</Text>
      </Screen>
    );
  }
  return <CardDetail key={card.id} card={card} />;
}

function CardDetail({ card }: { card: Card }) {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const members = useMembers();
  const canEdit = useSpace()?.role !== 'viewer';
  const { data: columns } = useQuery<Column>(
    `SELECT ${COLUMN_COLUMNS} FROM columns WHERE board_id = (SELECT board_id FROM columns WHERE id = ?) ORDER BY position, id`,
    [card.column_id],
  );
  const { data: siblings } = useQuery<{ n: number }>('SELECT count(*) AS n FROM cards WHERE column_id = ?', [card.column_id]);
  const [title, setTitle] = useState(card.title);
  const [note, setNote] = useState(card.note);
  const [due, setDue] = useState<string | null>(card.due);
  const [assignee, setAssignee] = useState<string | null>(card.assignee);
  const [column, setColumn] = useState<number | null>(card.column_id);
  const count = siblings?.[0]?.n ?? 0;

  async function save() {
    if (!title.trim()) return;
    await db.runAsync('UPDATE cards SET title = ?, note = ?, due = ?, assignee = ? WHERE id = ?', [title.trim(), note.trim(), due, assignee, card.id]);
    if (column !== null && column !== card.column_id) await runAction('moveCard', { card_id: card.id, column_id: column, position: 1_000_000 });
    router.back();
  }

  async function remove() {
    await runAction('deleteCard', { card_id: card.id });
    router.back();
  }

  const move = (position: number) => void runAction('moveCard', { card_id: card.id, column_id: card.column_id, position });

  return (
    <Screen>
      <Stack.Screen options={{ title: card.title }} />
      <TextField label="Title" value={title} onChangeText={setTitle} editable={canEdit} testID="card-title" />
      <TextField label="Note" value={note} onChangeText={setNote} multiline editable={canEdit} testID="card-note" />
      {canEdit ? (
        <>
          <Select label="Column" value={column} options={(columns ?? []).map((c) => ({ label: c.name, value: c.id }))} onChange={setColumn} testID="card-column" />
          <DateField label="Due" value={due} onChange={setDue} testID="card-due" />
          <Select
            label="Who's doing it"
            value={assignee}
            options={members.map((m) => ({ label: m.name, value: m.id }))}
            onChange={setAssignee}
            placeholder="Nobody"
            testID="card-assignee"
          />
          <View style={{ flexDirection: 'row', gap: t.spacing.sm }}>
            <View style={{ flex: 1 }}>
              <Button title="Move up" variant="secondary" icon="arrow-up-outline" disabled={card.position === 0} onPress={() => move(card.position - 1)} testID="card-up" />
            </View>
            <View style={{ flex: 1 }}>
              <Button title="Move down" variant="secondary" icon="arrow-down-outline" disabled={card.position >= count - 1} onPress={() => move(card.position + 1)} testID="card-down" />
            </View>
          </View>
          <Button title="Save" onPress={save} disabled={!title.trim()} testID="card-save" />
          <Button title="Delete card" variant="destructive" onPress={remove} testID="card-delete" />
        </>
      ) : null}
    </Screen>
  );
}
