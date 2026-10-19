import { Tabs, useRouter } from 'expo-router';
import { useQuery, useSpace } from '@homeai/sdk';
import { IconButton } from '@homeai/ui';
import { today } from '../../lib/chores';

export default function TabsLayout() {
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const { data } = useQuery<{ n: number }>('SELECT count(*) AS n FROM chores WHERE next_due <= ?', [today()]);
  const due = data?.[0]?.n ?? 0;
  const add = () =>
    canEdit ? (
      <IconButton icon="add-outline" accessibilityLabel="New chore" onPress={() => router.push('/new')} testID="new-chore" />
    ) : null;
  return (
    <Tabs screenOptions={{ headerRight: add }}>
      <Tabs.Screen name="index" options={{ title: 'Due', icon: 'today-outline', tabBarBadge: due || undefined }} />
      <Tabs.Screen name="all" options={{ title: 'All chores', icon: 'list-outline' }} />
      <Tabs.Screen name="history" options={{ title: 'History', icon: 'time-outline' }} />
    </Tabs>
  );
}
