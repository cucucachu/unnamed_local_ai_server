import { Stack } from 'expo-router';

export default function RootLayout() {
  return (
    <Stack>
      <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
      <Stack.Screen name="deck/[id]/index" options={{ title: 'Deck' }} />
      <Stack.Screen name="deck/[id]/edit" options={{ title: 'Edit deck' }} />
      <Stack.Screen name="card/[id]" options={{ title: 'Card' }} />
      <Stack.Screen name="study/[deck]" options={{ title: 'Study', presentation: 'modal' }} />
    </Stack>
  );
}
