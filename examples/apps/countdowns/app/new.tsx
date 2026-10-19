import { useRouter } from 'expo-router';
import { useDatabase } from '@homeai/sdk';
import { Screen } from '@homeai/ui';
import { CountdownForm, type CountdownValues } from '../components/CountdownForm';

export default function NewCountdownScreen() {
  const db = useDatabase();
  const router = useRouter();

  async function create(v: CountdownValues) {
    await db.runAsync('INSERT INTO countdowns (title, day, yearly, note) VALUES (?, ?, ?, ?)', [v.title, v.day, v.yearly, v.note]);
    router.back();
  }

  return (
    <Screen>
      <CountdownForm initial={{ title: '', day: '', yearly: 0, note: '' }} saveTitle="Add" onSave={create} />
    </Screen>
  );
}
