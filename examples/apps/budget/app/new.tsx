import { useRouter } from 'expo-router';
import { runAction, useDatabase } from '@homeai/sdk';
import { Screen } from '@homeai/ui';
import { TransactionForm, type TransactionValues } from '../components/TransactionForm';
import { today } from '../lib/budget';

export default function NewTransactionScreen() {
  const db = useDatabase();
  const router = useRouter();

  async function create(v: TransactionValues, pattern: string | null) {
    const { lastInsertRowId } = await db.runAsync('INSERT INTO transactions (day, description, amount, category_id, note) VALUES (?, ?, ?, ?, ?)', [
      v.day,
      v.description,
      v.amount,
      v.category_id,
      v.note,
    ]);
    if (pattern) await runAction('categorize', { id: lastInsertRowId, category_id: v.category_id, pattern });
    router.back();
  }

  return (
    <Screen>
      <TransactionForm initial={{ day: today(), description: '', amount: 0, category_id: null, note: '' }} saveTitle="Add" onSave={create} />
    </Screen>
  );
}
