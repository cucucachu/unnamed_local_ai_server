import { Stack } from 'expo-router';

export default function Layout() {
  return (
    <Stack>
      <Stack.Screen name="index" options={{ title: 'Groceries' }} />
      <Stack.Screen name="item/[id]" options={{ title: 'Item' }} />
    </Stack>
  );
}
