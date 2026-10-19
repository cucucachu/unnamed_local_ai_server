import { useState } from 'react';
import { Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { runAction, useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { Button, EmptyState, List, ListRow, NumberField, Screen, Section, TextField, Toggle, formatMoney, useTheme } from '@homeai/ui';
import { CURRENCY, type Category } from '../../lib/budget';

export default function CategoriesScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const { data } = useQuery<Category & { rules: number }>(
    `SELECT c.id, c.name, c.monthly_budget, c.is_income, (SELECT count(*) FROM rules r WHERE r.category_id = c.id) AS rules
     FROM categories c ORDER BY c.is_income, c.name COLLATE NOCASE`,
  );
  const [name, setName] = useState('');
  const [budget, setBudget] = useState<number | null>(null);
  const [income, setIncome] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function add() {
    if (!name.trim()) return setError('Give it a name.');
    setError(null);
    await db.runAsync('INSERT INTO categories (name, monthly_budget, is_income) VALUES (?, ?, ?)', [name.trim(), income ? null : budget, income ? 1 : 0]);
    setName('');
    setBudget(null);
    setIncome(false);
  }

  const categories = data ?? [];
  const row = (c: Category & { rules: number }) => (
    <ListRow
      key={c.id}
      title={c.name}
      subtitle={[c.monthly_budget !== null ? `${formatMoney(c.monthly_budget, CURRENCY)} a month` : null, c.rules ? `${c.rules} rule${c.rules === 1 ? '' : 's'}` : null]
        .filter(Boolean)
        .join(' · ') || undefined}
      onPress={() => router.push({ pathname: '/category/[id]', params: { id: String(c.id) } })}
      testID={`cat-${c.id}`}
    />
  );

  return (
    <Screen>
      {categories.length ? (
        <>
          <Section title="Spending">
            <List>{categories.filter((c) => !c.is_income).map(row)}</List>
          </Section>
          {categories.some((c) => c.is_income) ? (
            <Section title="Income">
              <List>{categories.filter((c) => c.is_income).map(row)}</List>
            </Section>
          ) : null}
        </>
      ) : (
        <EmptyState
          icon="pricetags-outline"
          title="No categories yet"
          action={canEdit ? { title: 'Add the usual ones', onPress: () => void runAction('addStarterCategories', {}) } : undefined}
          testID="categories-empty"
        />
      )}
      {canEdit ? (
        <Section title="New category">
          <View style={{ gap: t.spacing.sm }}>
            <TextField label="Name" value={name} onChangeText={setName} placeholder="e.g. Pets" testID="cat-name" />
            <Toggle label="Income" value={income} onValueChange={setIncome} testID="cat-income" />
            {income ? null : <NumberField label="Monthly budget" value={budget} onChange={setBudget} min={0} decimals={2} hint="Optional" testID="cat-budget" />}
            {error ? (
              <Text role="alert" style={{ color: t.colors.danger }}>
                {error}
              </Text>
            ) : null}
            <Button title="Add category" variant="secondary" onPress={add} testID="cat-add" />
          </View>
        </Section>
      ) : null}
    </Screen>
  );
}
