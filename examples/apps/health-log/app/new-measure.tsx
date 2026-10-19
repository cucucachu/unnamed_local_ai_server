import { useRouter } from 'expo-router';
import { useDatabase } from '@homeai/sdk';
import { Screen } from '@homeai/ui';
import { MeasureForm, type MeasureValues } from '../components/MeasureForm';

export default function NewMeasureScreen() {
  const db = useDatabase();
  const router = useRouter();

  async function create(v: MeasureValues) {
    const { lastInsertRowId } = await db.runAsync('INSERT INTO measures (name, unit, goal, decimals, summary) VALUES (?, ?, ?, ?, ?)', [
      v.name,
      v.unit,
      v.goal,
      v.decimals,
      v.summary,
    ]);
    router.replace({ pathname: '/measure/[id]', params: { id: String(lastInsertRowId) } });
  }

  return (
    <Screen>
      <MeasureForm initial={{ name: '', unit: '', goal: null, decimals: 1, summary: 'last' }} saveTitle="Add measure" onSave={create} />
    </Screen>
  );
}
