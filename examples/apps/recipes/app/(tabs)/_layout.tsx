import { Tabs, useRouter } from 'expo-router';
import { useSpace } from '@homeai/sdk';
import { IconButton } from '@homeai/ui';

export default function TabsLayout() {
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  return (
    <Tabs>
      <Tabs.Screen
        name="index"
        options={{
          title: 'Recipes',
          icon: 'restaurant-outline',
          headerRight: () =>
            canEdit ? <IconButton icon="add-outline" accessibilityLabel="New recipe" onPress={() => router.push('/new')} testID="new-recipe" /> : null,
        }}
      />
      <Tabs.Screen name="plan" options={{ title: 'Meal plan', icon: 'calendar-outline' }} />
    </Tabs>
  );
}
