import { Text } from 'react-native';
import { useRouter } from 'expo-router';
import { useMembers, useQuery, useSpace } from '@homeai/sdk';
import { EmptyState, List, ListRow, Screen, useTheme } from '@homeai/ui';
import { CHORES_SQL, dueLabel, everyLabel, type Chore } from '../../lib/chores';

export default function AllChoresScreen() {
  const t = useTheme();
  const router = useRouter();
  const members = useMembers();
  const canEdit = useSpace()?.role !== 'viewer';
  const { data, error } = useQuery<Chore>(`${CHORES_SQL} ORDER BY c.next_due, c.name`);
  const name = (id: string | null) => members.find((m) => m.id === id)?.name;

  return (
    <Screen>
      {error ? <Text style={{ color: t.colors.danger }}>{error.message}</Text> : null}
      <List
        data={data}
        keyExtractor={(c) => c.id}
        empty={
          <EmptyState
            icon="home-outline"
            title="No chores yet"
            message="Add the jobs that come round again: bins, plants, the bathroom."
            action={canEdit ? { title: 'Add a chore', onPress: () => router.push('/new') } : undefined}
          />
        }
        renderItem={(c) => (
          <ListRow
            title={c.name}
            subtitle={[everyLabel(c.every_days), name(c.turn_user) && `next: ${name(c.turn_user)}`].filter(Boolean).join(' · ')}
            value={dueLabel(c.next_due)}
            onPress={() => router.push({ pathname: '/chore/[id]', params: { id: String(c.id) } })}
            testID={`chore-${c.id}`}
          />
        )}
        testID="chores"
      />
    </Screen>
  );
}
