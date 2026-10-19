import { Stack } from 'expo-router';

export default function RootLayout() {
  return (
    <Stack>
      <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
      <Stack.Screen name="recipe/[id]/index" options={{ title: 'Recipe' }} />
      <Stack.Screen name="recipe/[id]/edit" options={{ title: 'Edit recipe' }} />
      <Stack.Screen name="new" options={{ presentation: 'modal', title: 'New recipe' }} />
      <Stack.Screen name="plan/[day]" options={{ presentation: 'modal', title: 'Plan a meal' }} />
    </Stack>
  );
}
