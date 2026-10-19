import { useState } from 'react';
import { Text } from 'react-native';
import { useRouter } from 'expo-router';
import { runAction, useQuery, useSpace } from '@homeai/sdk';
import { Button, EmptyState, List, ListRow, Screen, Section, formatMoney, useTheme } from '@homeai/ui';
import { MonthPicker } from '../../components/MonthPicker';
import { CURRENCY, monthRange, thisMonth } from '../../lib/budget';

type Row = { id: number; day: string; description: string; amount: number; category: string | null };

export default function TransactionsScreen() {
  const t = useTheme();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const [month, setMonth] = useState(thisMonth);
  const [message, setMessage] = useState('');
  const { data } = useQuery<Row>(
    `SELECT t.id, t.day, t.description, t.amount, c.name AS category
     FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
     WHERE t.day BETWEEN ? AND ? ORDER BY t.day DESC, t.id DESC`,
    monthRange(month),
  );
  const { data: counts } = useQuery<{ uncategorized: number; rules: number }>(
    'SELECT (SELECT count(*) FROM transactions WHERE category_id IS NULL) AS uncategorized, (SELECT count(*) FROM rules) AS rules',
  );
  const { uncategorized = 0, rules = 0 } = counts?.[0] ?? {};
  const rows = data ?? [];
  const open = (id: number) => router.push({ pathname: '/transaction/[id]', params: { id: String(id) } });

  async function apply() {
    const { changes } = await runAction('applyRules', {});
    setMessage(changes ? `Categorized ${changes} transaction${changes === 1 ? '' : 's'}.` : 'No rule matches the rest.');
  }

  const row = (r: Row) => (
    <ListRow
      key={r.id}
      title={r.description || '(no description)'}
      subtitle={`${r.day} · ${r.category ?? 'Uncategorized'}`}
      value={formatMoney(r.amount, CURRENCY, { signed: true })}
      onPress={() => open(r.id)}
      testID={`txn-${r.id}`}
    />
  );

  return (
    <Screen>
      <MonthPicker month={month} onChange={setMonth} />
      {uncategorized > 0 ? (
        <Section title={`${uncategorized} uncategorized`} footer="Pick a category on one, with “Always use this category”, and the rest that match follow.">
          {canEdit && rules > 0 ? <Button title="Apply my rules" variant="secondary" icon="pricetags-outline" onPress={apply} testID="apply-rules" /> : null}
        </Section>
      ) : null}
      {message ? (
        <Text style={{ color: t.colors.textMuted }} testID="rules-message">
          {message}
        </Text>
      ) : null}
      <List
        data={rows}
        keyExtractor={(r) => r.id}
        renderItem={row}
        empty={<EmptyState icon="list-outline" title="No transactions this month" testID="transactions-empty" />}
        testID="transactions"
      />
    </Screen>
  );
}
