import { Text } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useDatabase, useQuery } from '@homeai/sdk';
import { Screen } from '@homeai/ui';
import { RecipeForm, type RecipeValues } from '../../../components/RecipeForm';
import { RECIPE_COLUMNS, type Recipe } from '../../../lib/recipes';

export default function EditRecipeScreen() {
  const db = useDatabase();
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Recipe>(`SELECT ${RECIPE_COLUMNS} FROM recipes WHERE id = ?`, [Number(id)]);
  const recipe = data?.[0];

  if (!recipe) {
    return (
      <Screen>
        <Text>{loading ? 'Loading…' : 'This recipe was deleted.'}</Text>
      </Screen>
    );
  }

  async function save(v: RecipeValues) {
    await db.runAsync('UPDATE recipes SET name = ?, cuisine = ?, servings = ?, minutes = ?, ingredients = ?, steps = ? WHERE id = ?', [
      v.name,
      v.cuisine,
      v.servings,
      v.minutes,
      v.ingredients,
      v.steps,
      recipe!.id,
    ]);
    router.back();
  }

  return (
    <Screen>
      <RecipeForm key={recipe.id} initial={recipe} saveTitle="Save" onSave={save} />
    </Screen>
  );
}
