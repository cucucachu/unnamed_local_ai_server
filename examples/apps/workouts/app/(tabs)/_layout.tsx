import { Tabs, useRouter } from 'expo-router';
import { useDatabase, useSpace } from '@homeai/sdk';
import { IconButton } from '@homeai/ui';
import { today } from '../../lib/workouts';

export default function TabsLayout() {
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';

  async function start() {
    const { lastInsertRowId } = await db.runAsync('INSERT INTO workouts (day) VALUES (?)', [today()]);
    router.push({ pathname: '/workout/[id]', params: { id: String(lastInsertRowId) } });
  }

  return (
    <Tabs>
      <Tabs.Screen
        name="index"
        options={{
          title: 'Workouts',
          icon: 'barbell-outline',
          headerRight: () => (canEdit ? <IconButton icon="add-outline" accessibilityLabel="Start a workout" onPress={start} testID="new-workout" /> : null),
        }}
      />
      <Tabs.Screen name="exercises" options={{ title: 'Exercises', icon: 'trending-up-outline' }} />
    </Tabs>
  );
}
