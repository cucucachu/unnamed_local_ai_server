import { useState } from 'react';
import { Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useQuery } from '@homeai/sdk';
import { Card, EmptyState, List, ListRow, Money, PieChart, ProgressBar, Screen, Section, formatMoney, useTheme } from '@homeai/ui';
import { MonthPicker } from '../../components/MonthPicker';
import { CURRENCY, monthRange, thisMonth } from '../../lib/budget';

type Totals = { spent: number; income: number; n: number };
type ByCategory = { id: number | null; name: string; spent: number; monthly_budget: number | null };

export default function MonthScreen() {
  const t = useTheme();
  const router = useRouter();
  const [month, setMonth] = useState(thisMonth);
  const range = monthRange(month);
  const { data: totals } = useQuery<Totals>(
    `SELECT coalesce(-sum(CASE WHEN amount < 0 THEN amount END), 0) AS spent,
            coalesce(sum(CASE WHEN amount > 0 THEN amount END), 0) AS income, count(*) AS n
     FROM transactions WHERE day BETWEEN ? AND ?`,
    range,
  );
  // Spending per category this month, plus every budgeted category even if nothing was spent.
  const { data: byCategory } = useQuery<ByCategory>(
    `SELECT c.id, c.name, coalesce(-sum(t.amount), 0) AS spent, c.monthly_budget
     FROM categories c LEFT JOIN transactions t ON t.category_id = c.id AND t.amount < 0 AND t.day BETWEEN ? AND ?
     WHERE c.is_income = 0
     GROUP BY c.id HAVING spent > 0 OR c.monthly_budget IS NOT NULL
     UNION ALL
     SELECT NULL, 'Uncategorized', -sum(amount), NULL FROM transactions
     WHERE category_id IS NULL AND amount < 0 AND day BETWEEN ? AND ? GROUP BY category_id
     ORDER BY spent DESC`,
    [...range, ...range],
  );
  const sum = totals?.[0] ?? { spent: 0, income: 0, n: 0 };
  const budgeted = (byCategory ?? []).filter((c) => c.monthly_budget !== null);

  return (
    <Screen>
      <MonthPicker month={month} onChange={setMonth} />
      <Card>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
          <View>
            <Text style={{ color: t.colors.textMuted }}>Spent</Text>
            <Money amount={sum.spent} currency={CURRENCY} style={{ fontSize: t.font.large }} testID="month-spent" />
          </View>
          <View>
            <Text style={{ color: t.colors.textMuted }}>Income</Text>
            <Money amount={sum.income} currency={CURRENCY} style={{ fontSize: t.font.large }} testID="month-income" />
          </View>
          <View>
            <Text style={{ color: t.colors.textMuted }}>Left</Text>
            <Money amount={sum.income - sum.spent} currency={CURRENCY} colored style={{ fontSize: t.font.large }} testID="month-net" />
          </View>
        </View>
      </Card>
      {sum.n === 0 ? (
        <EmptyState
          icon="wallet-outline"
          title="Nothing this month"
          message="Add transactions with +, or import a bank statement (CSV): App info (ⓘ above), then Data."
          testID="month-empty"
        />
      ) : (
        <Section title="Where it went">
          <Card>
            <PieChart
              data={byCategory ?? []}
              label="name"
              value="spent"
              title="Spending by category"
              format={(n) => formatMoney(n, CURRENCY)}
              testID="spend-pie"
            />
          </Card>
        </Section>
      )}
      {budgeted.length ? (
        <Section title="Budgets">
          <List>
            {budgeted.map((c) => {
              const share = c.monthly_budget ? c.spent / c.monthly_budget : 0;
              return (
                <ListRow
                  key={c.id ?? 'none'}
                  title={c.name}
                  subtitle={`${formatMoney(c.spent, CURRENCY)} of ${formatMoney(c.monthly_budget ?? 0, CURRENCY)}${share > 1 ? ' · over' : ''}`}
                  right={
                    <View style={{ width: 80 }}>
                      <ProgressBar value={Math.min(1, share)} color={share > 1 ? t.colors.danger : undefined} />
                    </View>
                  }
                  onPress={c.id === null ? undefined : () => router.push({ pathname: '/category/[id]', params: { id: String(c.id) } })}
                  testID={`budget-${c.id}`}
                />
              );
            })}
          </List>
        </Section>
      ) : null}
    </Screen>
  );
}
