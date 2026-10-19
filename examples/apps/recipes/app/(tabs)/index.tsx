import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'expo-router';
import { search, useQuery } from '@homeai/sdk';
import { EmptyState, List, ListRow, Screen, SearchField } from '@homeai/ui';
import { RECIPE_COLUMNS, summary, type Recipe } from '../../lib/recipes';

export default function RecipesScreen() {
  const router = useRouter();
  const { data } = useQuery<Recipe>(`SELECT ${RECIPE_COLUMNS} FROM recipes ORDER BY name COLLATE NOCASE`);
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<number[] | null>(null);

  // By meaning and by words, over the name, cuisine, ingredients and steps (homeai.index).
  useEffect(() => {
    if (!query.trim()) return setFound(null);
    let live = true;
    const timer = setTimeout(() => {
      search(query, 50).then(
        (hits) => live && setFound(hits.filter((h) => h.table === 'recipes').map((h) => h.id)),
        () => live && setFound([]),
      );
    }, 300);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [query]);

  const shown = useMemo(() => {
    const all = data ?? [];
    return found ? found.map((id) => all.find((r) => r.id === id)).filter((r): r is Recipe => !!r) : all;
  }, [data, found]);

  return (
    <Screen>
      <SearchField value={query} onChangeText={setQuery} placeholder="Search recipes and ingredients" testID="search" />
      <List
        data={shown}
        keyExtractor={(r) => r.id}
        empty={
          query ? (
            <EmptyState icon="search-outline" title="Nothing found" testID="search-empty" />
          ) : (
            <EmptyState icon="restaurant-outline" title="No recipes yet" message="Add the dishes you make, with what goes in them." testID="recipes-empty" />
          )
        }
        renderItem={(r) => (
          <ListRow
            title={r.name}
            subtitle={summary(r)}
            onPress={() => router.push({ pathname: '/recipe/[id]', params: { id: String(r.id) } })}
            testID={`recipe-${r.id}`}
          />
        )}
        testID="recipes"
      />
    </Screen>
  );
}
