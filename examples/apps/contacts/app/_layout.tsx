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
          title: 'Contacts',
          headerRight: () =>
            canEdit ? <IconButton icon="person-add-outline" accessibilityLabel="New contact" onPress={() => router.push('/new')} testID="new-person" /> : null,
        }}
      />
      <Stack.Screen name="person/[id]" options={{ title: 'Contact' }} />
      <Stack.Screen name="new" options={{ presentation: 'modal', title: 'New contact' }} />
    </Stack>
  );
}
