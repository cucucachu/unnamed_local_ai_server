import { Stack, useRouter } from 'expo-router';
import { useSpace } from '@homeai/sdk';
import { IconButton } from '@homeai/ui';

export default function RootLayout() {
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  return (
    <Stack>
      <Stack.Screen
        name="index"
        options={{
          title: 'Countdowns',
          headerRight: () => (canEdit ? <IconButton icon="add-outline" accessibilityLabel="New countdown" onPress={() => router.push('/new')} testID="new-countdown" /> : null),
        }}
      />
      <Stack.Screen name="countdown/[id]" options={{ title: 'Countdown' }} />
      <Stack.Screen name="new" options={{ presentation: 'modal', title: 'New countdown' }} />
    </Stack>
  );
}
