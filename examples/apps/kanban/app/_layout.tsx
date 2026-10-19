import { Stack } from 'expo-router';

export default function RootLayout() {
  return (
    <Stack>
      <Stack.Screen name="index" options={{ title: 'Boards' }} />
      <Stack.Screen name="board/[id]/index" options={{ title: 'Board' }} />
      <Stack.Screen name="board/[id]/columns" options={{ title: 'Columns' }} />
      <Stack.Screen name="card/[id]" options={{ title: 'Card' }} />
    </Stack>
  );
}
