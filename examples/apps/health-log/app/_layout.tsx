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
          title: 'Health log',
          headerRight: () => (canEdit ? <IconButton icon="add-outline" accessibilityLabel="Log a reading" onPress={() => router.push('/log')} testID="new-entry" /> : null),
        }}
      />
      <Stack.Screen name="measure/[id]" options={{ title: 'Measure' }} />
      <Stack.Screen name="log" options={{ presentation: 'modal', title: 'Log a reading' }} />
      <Stack.Screen name="new-measure" options={{ presentation: 'modal', title: 'New measure' }} />
    </Stack>
  );
}
