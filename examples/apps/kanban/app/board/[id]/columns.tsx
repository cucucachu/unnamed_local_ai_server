import { useState } from 'react';
import { Text, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { runAction, useDatabase, useQuery } from '@homeai/sdk';
import { Button, Card, IconButton, NumberField, Screen, Section, TextField, useTheme } from '@homeai/ui';
import { COLUMN_COLUMNS, type Board, type Column } from '../../../lib/kanban';

export default function ColumnsScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data: boards, loading } = useQuery<Board>('SELECT id, name FROM boards WHERE id = ?', [Number(id)]);
  const { data: columns } = useQuery<Column & { cards: number }>(
    `SELECT ${COLUMN_COLUMNS}, (SELECT count(*) FROM cards WHERE column_id = columns.id) AS cards FROM columns WHERE board_id = ? ORDER BY position, id`,
    [Number(id)],
  );
  const [name, setName] = useState('');
  const board = boards?.[0];
  if (!board) {
    return (
      <Screen>
        <Text testID="board-missing">{loading ? 'Loading…' : 'This board was deleted.'}</Text>
      </Screen>
    );
  }

  async function addColumn() {
    if (!name.trim()) return;
    await db.runAsync('INSERT INTO columns (board_id, name, position) VALUES (?, ?, (SELECT count(*) FROM columns WHERE board_id = ?))', [board!.id, name.trim(), board!.id]);
    setName('');
  }

  async function removeBoard() {
    await db.runAsync('DELETE FROM boards WHERE id = ?', [board!.id]);
    router.dismissAll();
  }

  return (
    <Screen>
      <BoardName key={board.id} board={board} />
      {(columns ?? []).map((c, i) => (
        <ColumnEditor key={c.id} column={c} cards={c.cards} first={i === 0} last={i === (columns?.length ?? 0) - 1} />
      ))}
      <Section title="New column">
        <Card>
          <View style={{ gap: t.spacing.sm }}>
            <TextField label="Name" value={name} onChangeText={setName} placeholder="e.g. Waiting" onSubmitEditing={addColumn} testID="column-name" />
            <Button title="Add column" onPress={addColumn} disabled={!name.trim()} testID="column-add" />
          </View>
        </Card>
      </Section>
      <Button title="Delete board" variant="destructive" onPress={removeBoard} testID="board-delete" />
    </Screen>
  );
}

function BoardName({ board }: { board: Board }) {
  const db = useDatabase();
  const [name, setName] = useState(board.name);
  return (
    <Section title="Board">
      <Card>
        <TextField label="Name" value={name} onChangeText={setName} testID="board-edit-name" />
        <Button title="Rename" variant="secondary" disabled={!name.trim() || name.trim() === board.name} onPress={() => void db.runAsync('UPDATE boards SET name = ? WHERE id = ?', [name.trim(), board.id])} testID="board-rename" />
      </Card>
    </Section>
  );
}

function ColumnEditor({ column, cards, first, last }: { column: Column; cards: number; first: boolean; last: boolean }) {
  const t = useTheme();
  const db = useDatabase();
  const [name, setName] = useState(column.name);
  const [limit, setLimit] = useState<number | null>(column.wip_limit);
  const changed = name.trim() !== column.name || limit !== column.wip_limit;
  const move = (position: number) => void runAction('moveColumn', { column_id: column.id, position });

  return (
    <Section title={column.name}>
      <Card>
        <View style={{ gap: t.spacing.sm }}>
          <TextField label="Name" value={name} onChangeText={setName} testID={`col-${column.id}-name`} />
          <NumberField label="Card limit" value={limit} onChange={setLimit} min={1} decimals={0} placeholder="No limit" hint="Warns when the column holds more" testID={`col-${column.id}-limit`} />
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: t.spacing.sm }}>
            <IconButton icon="arrow-back-outline" accessibilityLabel={`Move ${column.name} left`} disabled={first} onPress={() => move(column.position - 1)} testID={`col-${column.id}-left`} />
            <IconButton icon="arrow-forward-outline" accessibilityLabel={`Move ${column.name} right`} disabled={last} onPress={() => move(column.position + 1)} testID={`col-${column.id}-right`} />
            <View style={{ flex: 1 }} />
            <Button
              title="Save"
              variant="secondary"
              disabled={!changed || !name.trim()}
              onPress={() => void db.runAsync('UPDATE columns SET name = ?, wip_limit = ? WHERE id = ?', [name.trim(), limit && limit > 0 ? limit : null, column.id])}
              testID={`col-${column.id}-save`}
            />
          </View>
          <Button
            title={cards ? `Delete column and its ${cards} card${cards === 1 ? '' : 's'}` : 'Delete column'}
            variant="destructive"
            onPress={() => void runAction('deleteColumn', { column_id: column.id })}
            testID={`col-${column.id}-delete`}
          />
        </View>
      </Card>
    </Section>
  );
}
