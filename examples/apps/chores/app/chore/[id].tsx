import { Text } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { runAction, useDatabase, useMembers, useQuery, useSpace } from '@homeai/sdk';
import { Button, EmptyState, List, ListRow, Screen, Section, useTheme } from '@homeai/ui';
import { ChoreForm, type ChoreValues } from '../../components/ChoreForm';
import { CHORES_SQL, dueLabel, today, type Chore } from '../../lib/chores';

type Done = { id: number; done_on: string; _created_by: string };

export default function ChoreScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Chore>(`${CHORES_SQL} WHERE c.id = ?`, [Number(id)]);
  const { data: assignees } = useQuery<{ user_id: string }>(
    'SELECT user_id FROM chore_assignees WHERE chore_id = ? ORDER BY position, id',
    [Number(id)],
  );
  const chore = data?.[0];
  if (!chore || !assignees) {
    return (
      <Screen>
        <Text testID="chore-missing">{loading ? 'Loading…' : 'This chore was deleted.'}</Text>
      </Screen>
    );
  }
  // `key` gives the form fresh state when someone else changes the chore.
  return <ChoreDetail key={`${chore.id}:${chore.next_due}`} chore={chore} assignees={assignees.map((a) => a.user_id)} />;
}

function ChoreDetail({ chore, assignees }: { chore: Chore; assignees: string[] }) {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const members = useMembers();
  const canEdit = useSpace()?.role !== 'viewer';
  const { data: history } = useQuery<Done>(
    'SELECT id, done_on, _created_by FROM completions WHERE chore_id = ? ORDER BY done_on DESC, id DESC LIMIT 20',
    [chore.id],
  );
  const who = (id: string | null) => members.find((m) => m.id === id)?.name;

  async function save(v: ChoreValues) {
    await db.runAsync('UPDATE chores SET name = ?, every_days = ?, next_due = ?, note = ? WHERE id = ?', [
      v.name,
      v.every_days,
      v.next_due,
      v.note,
      chore.id,
    ]);
    await db.runAsync('DELETE FROM chore_assignees WHERE chore_id = ?', [chore.id]);
    for (const [position, user] of v.assignees.entries()) {
      await db.runAsync('INSERT INTO chore_assignees (chore_id, user_id, position) VALUES (?, ?, ?)', [chore.id, user, position]);
    }
    router.back();
  }

  async function remove() {
    await db.runAsync('DELETE FROM chores WHERE id = ?', [chore.id]);
    router.back();
  }

  return (
    <Screen>
      <Stack.Screen options={{ title: chore.name }} />
      <Text style={{ color: t.colors.textMuted }} testID="chore-status">
        {[dueLabel(chore.next_due), who(chore.turn_user) && `${who(chore.turn_user)}'s turn`].filter(Boolean).join(' · ')}
      </Text>
      {canEdit ? (
        <Button
          title="Done"
          icon="checkmark-outline"
          onPress={() => void runAction('completeChore', { chore_id: chore.id, today: today() })}
          testID="chore-done"
        />
      ) : null}
      <ChoreForm
        initial={{ name: chore.name, every_days: chore.every_days, next_due: chore.next_due, note: chore.note, assignees }}
        saveTitle="Save"
        onSave={save}
        editable={canEdit}
      />
      <Section title="Done by">
        <List
          data={history}
          keyExtractor={(d) => d.id}
          empty={<EmptyState icon="time-outline" title="Not done yet" />}
          renderItem={(d) => <ListRow title={who(d._created_by) ?? 'Someone'} value={d.done_on} testID={`chore-history-${d.id}`} />}
        />
      </Section>
      {canEdit ? <Button title="Delete chore" variant="destructive" onPress={remove} testID="chore-delete" /> : null}
    </Screen>
  );
}
