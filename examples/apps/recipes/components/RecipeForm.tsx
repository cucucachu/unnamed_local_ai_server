import { useState } from 'react';
import { Text, View } from 'react-native';
import { Button, NumberField, TextField, useTheme } from '@homeai/ui';
import type { Recipe } from '../lib/recipes';

export type RecipeValues = Omit<Recipe, 'id'>;

export const EMPTY_RECIPE: RecipeValues = { name: '', cuisine: '', servings: 4, minutes: null, ingredients: '', steps: '' };

export function RecipeForm({ initial, saveTitle, onSave }: { initial: RecipeValues; saveTitle: string; onSave: (v: RecipeValues) => Promise<void> }) {
  const t = useTheme();
  const [name, setName] = useState(initial.name);
  const [cuisine, setCuisine] = useState(initial.cuisine);
  const [servings, setServings] = useState<number | null>(initial.servings);
  const [minutes, setMinutes] = useState<number | null>(initial.minutes);
  const [ingredients, setIngredients] = useState(initial.ingredients);
  const [steps, setSteps] = useState(initial.steps);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!name.trim()) return setError('Give the recipe a name.');
    setError(null);
    setSaving(true);
    try {
      await onSave({
        name: name.trim(),
        cuisine: cuisine.trim(),
        servings: Math.max(1, Math.round(servings ?? 1)),
        minutes: minutes === null ? null : Math.round(minutes),
        ingredients: ingredients.trim(),
        steps: steps.trim(),
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <View style={{ gap: t.spacing.md }}>
      <TextField label="Name" value={name} onChangeText={setName} placeholder="e.g. Dal tadka" testID="recipe-name" />
      <TextField label="Cuisine" value={cuisine} onChangeText={setCuisine} placeholder="e.g. Indian" testID="recipe-cuisine" />
      <View style={{ flexDirection: 'row', gap: t.spacing.md }}>
        <View style={{ flex: 1 }}>
          <NumberField label="Serves" value={servings} onChange={setServings} min={1} decimals={0} testID="recipe-servings" />
        </View>
        <View style={{ flex: 1 }}>
          <NumberField label="Minutes" value={minutes} onChange={setMinutes} min={0} decimals={0} testID="recipe-minutes" />
        </View>
      </View>
      <TextField
        label="Ingredients"
        value={ingredients}
        onChangeText={setIngredients}
        multiline
        hint="One per line, e.g. 200 g red lentils"
        testID="recipe-ingredients"
      />
      <TextField label="Steps" value={steps} onChangeText={setSteps} multiline testID="recipe-steps" />
      {error ? (
        <Text role="alert" style={{ color: t.colors.danger }} testID="recipe-error">
          {error}
        </Text>
      ) : null}
      <Button title={saveTitle} onPress={save} loading={saving} testID="recipe-save" />
    </View>
  );
}
