import { useRouter } from 'expo-router';
import { useDatabase } from '@homeai/sdk';
import { Screen } from '@homeai/ui';
import { PersonForm, type PersonValues } from '../components/PersonForm';

export default function NewPersonScreen() {
  const db = useDatabase();
  const router = useRouter();

  async function create(v: PersonValues) {
    const { lastInsertRowId } = await db.runAsync('INSERT INTO people (name, birthday, notes) VALUES (?, ?, ?)', [v.name, v.birthday, v.notes]);
    router.replace({ pathname: '/person/[id]', params: { id: String(lastInsertRowId) } });
  }

  return (
    <Screen>
      <PersonForm initial={{ name: '', birthday: null, notes: '' }} saveTitle="Add contact" onSave={create} />
    </Screen>
  );
}
