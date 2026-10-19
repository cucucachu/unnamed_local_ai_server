import { useState } from 'react';
import { Text, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { runAction, useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { Button, Card, List, ListRow, NumberField, Screen, Section, Select, Stepper, TextField, useTheme } from '@homeai/ui';
import { dayLabel, setLabel, today, type Exercise } from '../../lib/workouts';

type Workout = { id: number; day: string; note: string };
type Done = { id: number; exercise_id: number; reps: number; weight: number; name: string; unit: string };

export default function WorkoutScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Workout>('SELECT id, day, note FROM workouts WHERE id = ?', [Number(id)]);
  const workout = data?.[0];
  if (!workout) {
    return (
      <Screen>
        <Text testID="workout-missing">{loading ? 'Loading…' : 'This workout was deleted.'}</Text>
      </Screen>
    );
  }
  return <WorkoutDetail key={workout.id} workout={workout} />;
}

function WorkoutDetail({ workout }: { workout: Workout }) {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const { data: exercises } = useQuery<Exercise>('SELECT id, name, unit, note FROM exercises ORDER BY name COLLATE NOCASE');
  const { data: sets } = useQuery<Done>(
    `SELECT s.id, s.exercise_id, s.reps, s.weight, e.name, e.unit
     FROM sets s JOIN exercises e ON e.id = s.exercise_id
     WHERE s.workout_id = ? ORDER BY s.position, s.id`,
    [workout.id],
  );
  const [exercise, setExercise] = useState<number | null>(null);
  const [reps, setReps] = useState(5);
  const [weight, setWeight] = useState<number | null>(null);
  const [note, setNote] = useState(workout.note);
  const [message, setMessage] = useState('');

  // Sets grouped by exercise, in the order the exercises were first done.
  const groups: { exercise_id: number; name: string; unit: string; sets: Done[] }[] = [];
  for (const s of sets ?? []) {
    let g = groups.find((x) => x.exercise_id === s.exercise_id);
    if (!g) groups.push((g = { exercise_id: s.exercise_id, name: s.name, unit: s.unit, sets: [] }));
    g.sets.push(s);
  }
  const chosen = (exercises ?? []).find((e) => e.id === exercise);

  async function addSet() {
    if (exercise === null) return setMessage('Pick an exercise.');
    setMessage('');
    await runAction('addSet', { workout_id: workout.id, exercise_id: exercise, reps, weight: weight ?? 0 });
  }

  async function repeat() {
    await runAction('repeatWorkout', { workout_id: workout.id, day: today() });
    const [latest] = await db.getAllAsync<{ id: number }>('SELECT max(id) AS id FROM workouts');
    router.replace({ pathname: '/workout/[id]', params: { id: String(latest.id) } });
  }

  async function remove() {
    await db.runAsync('DELETE FROM workouts WHERE id = ?', [workout.id]);
    router.back();
  }

  return (
    <Screen>
      <Stack.Screen options={{ title: dayLabel(workout.day) }} />
      {groups.length ? (
        groups.map((g) => (
          <Section key={g.exercise_id} title={g.name}>
            <List>
              {g.sets.map((s, i) => (
                <ListRow
                  key={s.id}
                  title={setLabel(s, g.unit)}
                  subtitle={`Set ${i + 1}`}
                  actions={canEdit ? [{ label: 'Delete', icon: 'trash-outline', onPress: () => void db.runAsync('DELETE FROM sets WHERE id = ?', [s.id]) }] : undefined}
                  testID={`set-${s.id}`}
                />
              ))}
            </List>
          </Section>
        ))
      ) : (
        <Text style={{ color: t.colors.textMuted }} testID="workout-no-sets">
          No sets yet.
        </Text>
      )}
      {canEdit ? (
        <Section title="Add a set" footer="Long-press a set to delete it.">
          <Card>
            <View style={{ gap: t.spacing.sm }}>
              {exercises?.length ? (
                <Select label="Exercise" value={exercise} options={exercises.map((e) => ({ label: e.name, value: e.id }))} onChange={setExercise} placeholder="Pick one" testID="set-exercise" />
              ) : (
                <Button title="Add exercises first" variant="plain" onPress={() => router.push('/exercises')} testID="set-no-exercises" />
              )}
              <Stepper label="Reps" value={reps} onChange={setReps} min={0} testID="set-reps" />
              {chosen?.unit === '' ? null : (
                <NumberField label={`Weight${chosen?.unit ? ` (${chosen.unit})` : ''}`} value={weight} onChange={setWeight} min={0} decimals={2} testID="set-weight" />
              )}
              {message ? (
                <Text role="alert" style={{ color: t.colors.danger }}>
                  {message}
                </Text>
              ) : null}
              <Button title="Add set" onPress={addSet} disabled={!exercises?.length} testID="set-add" />
            </View>
          </Card>
        </Section>
      ) : null}
      <TextField label="Note" value={note} onChangeText={setNote} multiline editable={canEdit} testID="workout-note" />
      {canEdit ? (
        <View style={{ gap: t.spacing.sm }}>
          <Button title="Save note" variant="secondary" onPress={() => void db.runAsync('UPDATE workouts SET note = ? WHERE id = ?', [note.trim(), workout.id])} testID="workout-save-note" />
          {sets?.length ? <Button title="Repeat this workout today" variant="secondary" icon="repeat-outline" onPress={repeat} testID="workout-repeat" /> : null}
          <Button title="Delete workout" variant="destructive" onPress={remove} testID="workout-delete" />
        </View>
      ) : null}
    </Screen>
  );
}
