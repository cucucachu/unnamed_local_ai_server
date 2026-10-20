import { Stack } from 'expo-router';
import { useSpace } from '@homeai/sdk';

export default function RootLayout() {
  const space = useSpace();
  return (
    <Stack>
      <Stack.Screen name="index" options={{ title: space ? `${space.name} calendar` : 'Calendar' }} />
      <Stack.Screen name="event/[id]/index" options={{ title: 'Event' }} />
      <Stack.Screen name="event/[id]/edit" options={{ presentation: 'modal', title: 'Edit event' }} />
      <Stack.Screen name="new" options={{ presentation: 'modal', title: 'New event' }} />
    </Stack>
  );
}
