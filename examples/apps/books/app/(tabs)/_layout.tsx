import { Tabs, useRouter } from 'expo-router';
import { useSpace } from '@homeai/sdk';
import { IconButton } from '@homeai/ui';

export default function TabsLayout() {
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  return (
    <Tabs>
      <Tabs.Screen
        name="index"
        options={{
          title: 'Shelf',
          icon: 'library-outline',
          headerRight: () => (canEdit ? <IconButton icon="add-outline" accessibilityLabel="Add" onPress={() => router.push('/new')} testID="new-item" /> : null),
        }}
      />
      <Tabs.Screen name="year" options={{ title: 'This year', icon: 'trophy-outline' }} />
    </Tabs>
  );
}
