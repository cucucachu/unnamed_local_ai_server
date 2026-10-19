import { Stack } from 'expo-router';

export default function RootLayout() {
  return (
    <Stack>
      <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
      <Stack.Screen name="item/[id]" options={{ title: 'Details' }} />
      <Stack.Screen name="new" options={{ title: 'Add', presentation: 'modal' }} />
    </Stack>
  );
}
