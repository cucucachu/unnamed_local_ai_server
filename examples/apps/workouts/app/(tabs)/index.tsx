import { useRouter } from 'expo-router';
import { useQuery } from '@homeai/sdk';
import { EmptyState, List, ListRow, Screen } from '@homeai/ui';
import { dayLabel } from '../../lib/workouts';

type Row = { id: number; day: string; note: string; exercises: number; sets: number; names: string | null };

export default function WorkoutsScreen() {
  const router = useRouter();
  const { data } = useQuery<Row>(
    `SELECT w.id, w.day, w.note, count(DISTINCT s.exercise_id) AS exercises, count(s.id) AS sets,
            (SELECT group_concat(name, ', ') FROM (SELECT DISTINCT e.name FROM sets s2 JOIN exercises e ON e.id = s2.exercise_id WHERE s2.workout_id = w.id)) AS names
     FROM workouts w LEFT JOIN sets s ON s.workout_id = w.id
     GROUP BY w.id ORDER BY w.day DESC, w.id DESC LIMIT 200`,
  );

  return (
    <Screen>
      <List
        data={data ?? []}
        keyExtractor={(w) => w.id}
        empty={<EmptyState icon="barbell-outline" title="No workouts yet" message="Add your exercises, then start a workout with +." testID="workouts-empty" />}
        renderItem={(w) => (
          <ListRow
            title={dayLabel(w.day)}
            subtitle={w.sets ? `${w.names} · ${w.sets} set${w.sets === 1 ? '' : 's'}` : 'No sets yet'}
            onPress={() => router.push({ pathname: '/workout/[id]', params: { id: String(w.id) } })}
            testID={`workout-${w.id}`}
          />
        )}
        testID="workouts"
      />
    </Screen>
  );
}
