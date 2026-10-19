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
          title: 'Pantry',
          headerRight: () =>
            canEdit ? <IconButton icon="add-outline" accessibilityLabel="Add an item" onPress={() => router.push('/new')} testID="new-item" /> : null,
        }}
      />
      <Stack.Screen name="item/[id]" options={{ title: 'Item' }} />
      <Stack.Screen name="new" options={{ presentation: 'modal', title: 'Add to the pantry' }} />
    </Stack>
  );
}
