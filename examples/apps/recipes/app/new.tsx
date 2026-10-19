import { useRouter } from 'expo-router';
import { useDatabase } from '@homeai/sdk';
import { Screen } from '@homeai/ui';
import { EMPTY_RECIPE, RecipeForm, type RecipeValues } from '../components/RecipeForm';

export default function NewRecipeScreen() {
  const db = useDatabase();
  const router = useRouter();

  async function create(v: RecipeValues) {
    const { lastInsertRowId } = await db.runAsync(
      'INSERT INTO recipes (name, cuisine, servings, minutes, ingredients, steps) VALUES (?, ?, ?, ?, ?, ?)',
      [v.name, v.cuisine, v.servings, v.minutes, v.ingredients, v.steps],
    );
    router.replace({ pathname: '/recipe/[id]', params: { id: String(lastInsertRowId) } });
  }

  return (
    <Screen>
      <RecipeForm initial={EMPTY_RECIPE} saveTitle="Add recipe" onSave={create} />
    </Screen>
  );
}
