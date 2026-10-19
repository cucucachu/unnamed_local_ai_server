import { useState } from 'react';
import { Text } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { runAction, useDatabase, useQuery } from '@homeai/sdk';
import { Button, List, ListRow, Screen, Section, Select, TextField, useTheme } from '@homeai/ui';
import { SLOTS, dayLabel, localDate } from '../../lib/recipes';

type Option = { id: number; name: string };
type Meal = { id: number; slot: string; name: string | null; note: string };

/** The modal over the meal plan: pick a meal of the day and a recipe (or just a note). */
export default function PlanDayScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const params = useLocalSearchParams<{ day: string; recipe?: string }>();
  const day = /^\d{4}-\d{2}-\d{2}$/.test(params.day ?? '') ? params.day : localDate(new Date());
  const { data: recipes } = useQuery<Option>('SELECT id, name FROM recipes ORDER BY name COLLATE NOCASE');
  const { data: planned } = useQuery<Meal>(
    'SELECT m.id, m.slot, r.name, m.note FROM meals m LEFT JOIN recipes r ON r.id = m.recipe_id WHERE m.day = ? ORDER BY m.slot',
    [day],
  );
  const [slot, setSlot] = useState<string>('Dinner');
  const [recipe, setRecipe] = useState<number | null>(params.recipe ? Number(params.recipe) : null);
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);

  async function plan() {
    if (recipe === null && !note.trim()) return setError('Pick a recipe or write a note.');
    try {
      await runAction('planMeal', { day, slot, recipe_id: recipe, note });
      router.back();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <Screen>
      <Stack.Screen options={{ title: dayLabel(day) }} />
      <Select label="Meal" value={slot} options={SLOTS.map((s) => ({ label: s, value: s }))} onChange={setSlot} testID="plan-slot" />
      <Select
        label="Recipe"
        value={recipe}
        options={(recipes ?? []).map((r) => ({ label: r.name, value: r.id }))}
        onChange={setRecipe}
        placeholder={recipes?.length ? 'Pick a recipe' : 'No recipes yet'}
        testID="plan-recipe"
      />
      <TextField label="Note" value={note} onChangeText={setNote} placeholder="e.g. leftovers, eating out" testID="plan-note" />
      {error ? (
        <Text role="alert" style={{ color: t.colors.danger }} testID="plan-error">
          {error}
        </Text>
      ) : null}
      <Button title="Plan it" onPress={plan} testID="plan-save" />
      {planned?.length ? (
        <Section title="Already planned">
          <List>
            {planned.map((m) => (
              <ListRow
                key={m.id}
                title={[m.name, m.note].filter(Boolean).join(' · ') || m.slot}
                subtitle={m.slot}
                actions={[{ label: 'Remove', icon: 'trash-outline', onPress: () => void db.runAsync('DELETE FROM meals WHERE id = ?', [m.id]) }]}
                testID={`planned-${m.id}`}
              />
            ))}
          </List>
        </Section>
      ) : null}
    </Screen>
  );
}
