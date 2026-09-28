import { Stack } from 'expo-router';

export default function RootLayout() {
  return (
    <Stack>
      <Stack.Screen name="index" options={{ title: 'Tracker' }} />
      <Stack.Screen name="habit/[id]" options={{ title: 'Habit' }} />
    </Stack>
  );
}
