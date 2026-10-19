import { useRouter } from 'expo-router';
import { runAction, useDatabase, useUser } from '@homeai/sdk';
import { Screen } from '@homeai/ui';
import { ChoreForm, type ChoreValues } from '../components/ChoreForm';
import { today } from '../lib/chores';

export default function NewChoreScreen() {
  const db = useDatabase();
  const router = useRouter();
  const me = useUser();

  async function create(v: ChoreValues) {
    const { lastInsertRowId: id } = await runAction('addChore', { name: v.name, every_days: v.every_days, next_due: v.next_due });
    if (v.note) await db.runAsync('UPDATE chores SET note = ? WHERE id = ?', [v.note, id]);
    for (const [position, user] of v.assignees.entries()) {
      await db.runAsync('INSERT INTO chore_assignees (chore_id, user_id, position) VALUES (?, ?, ?)', [id, user, position]);
    }
    router.back();
  }

  return (
    <Screen>
      <ChoreForm
        initial={{ name: '', every_days: 7, next_due: today(), note: '', assignees: me ? [me.id] : [] }}
        saveTitle="Add chore"
        onSave={create}
      />
    </Screen>
  );
}
