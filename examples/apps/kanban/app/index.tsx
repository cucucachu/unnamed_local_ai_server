import { useState } from 'react';
import { View } from 'react-native';
import { useRouter } from 'expo-router';
import { runAction, useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { Button, Card, EmptyState, List, ListRow, Screen, Section, TextField, useTheme } from '@homeai/ui';

type Row = { id: number; name: string; cards: number; open: number };

export default function BoardsScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  // "Open" is every card but the ones in a board's last column (Done).
  const { data } = useQuery<Row>(
    `SELECT b.id, b.name, count(ca.id) AS cards,
            count(CASE WHEN co.position < (SELECT max(position) FROM columns WHERE board_id = b.id) THEN ca.id END) AS open
     FROM boards b LEFT JOIN columns co ON co.board_id = b.id LEFT JOIN cards ca ON ca.column_id = co.id
     GROUP BY b.id ORDER BY b.name COLLATE NOCASE`,
  );
  const [name, setName] = useState('');

  async function add() {
    if (!name.trim()) return;
    await runAction('newBoard', { name });
    const [b] = await db.getAllAsync<{ id: number }>('SELECT max(id) AS id FROM boards');
    setName('');
    router.push({ pathname: '/board/[id]', params: { id: String(b.id) } });
  }

  return (
    <Screen>
      <List
        data={data ?? []}
        keyExtractor={(b) => b.id}
        empty={<EmptyState icon="albums-outline" title="No boards yet" message="Make one below; it starts with To do, Doing and Done." testID="boards-empty" />}
        renderItem={(b) => (
          <ListRow
            title={b.name}
            subtitle={b.cards ? `${b.open} open of ${b.cards} card${b.cards === 1 ? '' : 's'}` : 'No cards yet'}
            onPress={() => router.push({ pathname: '/board/[id]', params: { id: String(b.id) } })}
            testID={`board-${b.id}`}
          />
        )}
        testID="boards"
      />
      {canEdit ? (
        <Section title="New board">
          <Card>
            <View style={{ gap: t.spacing.sm }}>
              <TextField label="Name" value={name} onChangeText={setName} placeholder="e.g. House projects" onSubmitEditing={add} testID="board-name" />
              <Button title="Add board" onPress={add} disabled={!name.trim()} testID="board-add" />
            </View>
          </Card>
        </Section>
      ) : null}
    </Screen>
  );
}
