import { Text, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { Button, Card, IconButton, Screen, Section, useTheme } from '@homeai/ui';
import { GroceryButton } from '../../../components/GroceryButton';
import { RECIPE_COLUMNS, ingredientLines, localDate, summary, type Recipe } from '../../../lib/recipes';

export default function RecipeScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Recipe>(`SELECT ${RECIPE_COLUMNS} FROM recipes WHERE id = ?`, [Number(id)]);
  const recipe = data?.[0];

  if (!recipe) {
    return (
      <Screen>
        <Text testID="recipe-missing">{loading ? 'Loading…' : 'This recipe was deleted.'}</Text>
      </Screen>
    );
  }
  const lines = ingredientLines(recipe.ingredients);

  async function remove() {
    await db.runAsync('DELETE FROM recipes WHERE id = ?', [recipe!.id]);
    router.back();
  }

  return (
    <Screen>
      <Stack.Screen
        options={{
          title: recipe.name,
          headerRight: () =>
            canEdit ? (
              <IconButton
                icon="create-outline"
                accessibilityLabel="Edit"
                onPress={() => router.push({ pathname: '/recipe/[id]/edit', params: { id: String(recipe.id) } })}
                testID="recipe-edit"
              />
            ) : null,
        }}
      />
      <Text style={{ color: t.colors.textMuted }} testID="recipe-summary">
        {summary(recipe)}
      </Text>
      <Section title="Ingredients">
        <Card>
          {lines.length ? (
            lines.map((line, i) => (
              <Text key={i} style={{ color: t.colors.text, paddingVertical: 2 }} testID={`ingredient-${i}`}>
                • {line}
              </Text>
            ))
          ) : (
            <Text style={{ color: t.colors.textMuted }}>None listed.</Text>
          )}
        </Card>
      </Section>
      {canEdit ? <GroceryButton lines={lines} /> : null}
      {recipe.steps ? (
        <Section title="Steps">
          <Card>
            <Text style={{ color: t.colors.text, lineHeight: 22 }} testID="recipe-steps-text">
              {recipe.steps}
            </Text>
          </Card>
        </Section>
      ) : null}
      {canEdit ? (
        <View style={{ gap: t.spacing.sm }}>
          <Button
            title="Plan it"
            icon="calendar-outline"
            variant="secondary"
            onPress={() => router.push({ pathname: '/plan/[day]', params: { day: localDate(new Date()), recipe: String(recipe.id) } })}
            testID="recipe-plan"
          />
          <Button title="Delete recipe" variant="destructive" onPress={remove} testID="recipe-delete" />
        </View>
      ) : null}
    </Screen>
  );
}
