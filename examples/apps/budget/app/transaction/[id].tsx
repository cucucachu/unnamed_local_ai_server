import { Text } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { runAction, useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { Button, Screen } from '@homeai/ui';
import { TransactionForm, type TransactionValues } from '../../components/TransactionForm';
import { TRANSACTION_COLUMNS, type Transaction } from '../../lib/budget';

export default function TransactionScreen() {
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Transaction>(`SELECT ${TRANSACTION_COLUMNS} FROM transactions WHERE id = ?`, [Number(id)]);
  const txn = data?.[0];

  if (!txn) {
    return (
      <Screen>
        <Text testID="txn-missing">{loading ? 'Loading…' : 'This transaction was deleted.'}</Text>
      </Screen>
    );
  }

  async function save(v: TransactionValues, pattern: string | null) {
    await db.runAsync('UPDATE transactions SET day = ?, description = ?, amount = ?, category_id = ?, note = ? WHERE id = ?', [
      v.day,
      v.description,
      v.amount,
      v.category_id,
      v.note,
      txn!.id,
    ]);
    if (pattern) await runAction('categorize', { id: txn!.id, category_id: v.category_id, pattern });
    router.back();
  }

  async function remove() {
    await db.runAsync('DELETE FROM transactions WHERE id = ?', [txn!.id]);
    router.back();
  }

  return (
    <Screen>
      <Stack.Screen options={{ title: txn.description || 'Transaction' }} />
      <TransactionForm key={txn.id} initial={txn} saveTitle="Save" onSave={save} editable={canEdit} />
      {canEdit ? <Button title="Delete transaction" variant="destructive" onPress={remove} testID="txn-delete" /> : null}
    </Screen>
  );
}
