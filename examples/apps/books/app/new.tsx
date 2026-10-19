import { useRouter } from 'expo-router';
import { useDatabase } from '@homeai/sdk';
import { Screen } from '@homeai/ui';
import { ItemForm, type ItemValues } from '../components/ItemForm';

export default function NewItemScreen() {
  const db = useDatabase();
  const router = useRouter();

  async function create(v: ItemValues) {
    await db.runAsync('INSERT INTO items (title, creator, kind, status, rating, started_on, finished_on, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', [
      v.title,
      v.creator,
      v.kind,
      v.status,
      v.rating,
      v.started_on,
      v.finished_on,
      v.notes,
    ]);
    router.back();
  }

  return (
    <Screen>
      <ItemForm initial={{ title: '', creator: '', kind: 'book', status: 'want', rating: null, started_on: null, finished_on: null, notes: '' }} saveTitle="Add" onSave={create} />
    </Screen>
  );
}
