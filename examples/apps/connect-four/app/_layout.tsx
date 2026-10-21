import { Stack, useRouter } from 'expo-router';
import { useSpace } from '@homeai/sdk';
import { IconButton } from '@homeai/ui';

export default function RootLayout() {
  const router = useRouter();
  const canPlay = useSpace()?.role !== 'viewer';
  return (
    <Stack>
      <Stack.Screen
        name="index"
        options={{
          title: 'Connect Four',
          headerRight: () => (canPlay ? <IconButton icon="add-outline" accessibilityLabel="New game" onPress={() => router.push('/new')} testID="new-game" /> : null),
        }}
      />
      <Stack.Screen name="game/[id]" options={{ title: 'Game' }} />
      <Stack.Screen name="new" options={{ presentation: 'modal', title: 'New game' }} />
    </Stack>
  );
}
