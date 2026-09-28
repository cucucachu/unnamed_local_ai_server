import { Stack } from 'expo-router';

export default function RootLayout() {
  return (
    <Stack>
      <Stack.Screen name="index" options={{ title: 'List' }} />
      <Stack.Screen name="item/[id]" options={{ title: 'Item' }} />
    </Stack>
  );
}
