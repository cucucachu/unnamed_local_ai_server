import { useState } from 'react';
import { Text, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { Button, EmptyState, List, ListRow, NumberField, Screen, Section, TextField, formatMoney, useTheme } from '@homeai/ui';
import { CURRENCY, monthRange, thisMonth, type Category } from '../../lib/budget';

export default function CategoryScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Category>('SELECT id, name, monthly_budget, is_income FROM categories WHERE id = ?', [Number(id)]);
  const category = data?.[0];
  if (!category) {
    return (
      <Screen>
        <Text testID="cat-missing">{loading ? 'Loading…' : 'This category was deleted.'}</Text>
      </Screen>
    );
  }
  return <CategoryDetail key={category.id} category={category} />;
}

function CategoryDetail({ category }: { category: Category }) {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const [name, setName] = useState(category.name);
  const [budget, setBudget] = useState<number | null>(category.monthly_budget);
  const { data: rules } = useQuery<{ id: number; pattern: string }>('SELECT id, pattern FROM rules WHERE category_id = ? ORDER BY pattern', [category.id]);
  const { data: recent } = useQuery<{ id: number; day: string; description: string; amount: number }>(
    'SELECT id, day, description, amount FROM transactions WHERE category_id = ? AND day BETWEEN ? AND ? ORDER BY day DESC, id DESC',
    [category.id, ...monthRange(thisMonth())],
  );

  async function save() {
    if (!name.trim()) return;
    await db.runAsync('UPDATE categories SET name = ?, monthly_budget = ? WHERE id = ?', [name.trim(), category.is_income ? null : budget, category.id]);
    router.back();
  }

  async function remove() {
    await db.runAsync('DELETE FROM categories WHERE id = ?', [category.id]);
    router.back();
  }

  return (
    <Screen>
      <Stack.Screen options={{ title: category.name }} />
      <View style={{ gap: t.spacing.md }}>
        <TextField label="Name" value={name} onChangeText={setName} editable={canEdit} testID="cat-edit-name" />
        {category.is_income ? null : (
          <NumberField label="Monthly budget" value={budget} onChange={setBudget} min={0} decimals={2} hint="Empty for no budget" testID="cat-edit-budget" />
        )}
        {canEdit ? <Button title="Save" onPress={save} testID="cat-save" /> : null}
      </View>
      <Section title="Rules" footer="Transactions whose description contains one of these go here. Long-press to remove one.">
        <List
          data={rules ?? []}
          keyExtractor={(r) => r.id}
          empty={<Text style={{ color: t.colors.textMuted, padding: t.spacing.md }}>None yet. Pick this category on a transaction with “Always use this category”.</Text>}
          renderItem={(r) => (
            <ListRow
              title={`Contains “${r.pattern}”`}
              actions={canEdit ? [{ label: 'Remove', icon: 'trash-outline', onPress: () => void db.runAsync('DELETE FROM rules WHERE id = ?', [r.id]) }] : undefined}
              testID={`rule-${r.id}`}
            />
          )}
        />
      </Section>
      <Section title="This month">
        <List
          data={recent ?? []}
          keyExtractor={(r) => r.id}
          empty={<EmptyState icon="list-outline" title="Nothing this month" />}
          renderItem={(r) => (
            <ListRow
              title={r.description || '(no description)'}
              subtitle={r.day}
              value={formatMoney(r.amount, CURRENCY, { signed: true })}
              onPress={() => router.push({ pathname: '/transaction/[id]', params: { id: String(r.id) } })}
            />
          )}
        />
      </Section>
      {canEdit ? <Button title="Delete category" variant="destructive" onPress={remove} testID="cat-delete" /> : null}
    </Screen>
  );
}
