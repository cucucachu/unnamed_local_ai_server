import { Stack } from 'expo-router';

export default function RootLayout() {
  return (
    <Stack>
      <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
      <Stack.Screen name="chore/[id]" options={{ title: 'Chore' }} />
      <Stack.Screen name="new" options={{ presentation: 'modal', title: 'New chore' }} />
    </Stack>
  );
}
