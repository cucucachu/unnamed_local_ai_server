import { useState } from 'react';
import { Text, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { BarChart, Button, Card, LineChart, List, ListRow, Screen, Section, TextField, useTheme } from '@homeai/ui';
import { dayLabel, round, setLabel, type Exercise } from '../../lib/workouts';

type Day = { workout_id: number; day: string; top: number; reps: number; volume: number };

export default function ExerciseScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Exercise>('SELECT id, name, unit, note FROM exercises WHERE id = ?', [Number(id)]);
  const exercise = data?.[0];
  if (!exercise) {
    return (
      <Screen>
        <Text testID="exercise-missing">{loading ? 'Loading…' : 'This exercise was deleted.'}</Text>
      </Screen>
    );
  }
  return <ExerciseDetail key={exercise.id} exercise={exercise} />;
}

function ExerciseDetail({ exercise }: { exercise: Exercise }) {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const [name, setName] = useState(exercise.name);
  const [note, setNote] = useState(exercise.note);
  // One point per workout: the heaviest set, the most reps, and the volume (reps × weight).
  const { data: days } = useQuery<Day>(
    `SELECT w.id AS workout_id, w.day, max(s.weight) AS top, max(s.reps) AS reps, sum(s.reps * s.weight) AS volume
     FROM sets s JOIN workouts w ON w.id = s.workout_id
     WHERE s.exercise_id = ? GROUP BY w.id ORDER BY w.day, w.id`,
    [exercise.id],
  );
  const points = (days ?? []).map((d) => ({ ...d, label: dayLabel(d.day), top: round(d.top), volume: round(d.volume) }));
  const bodyweight = exercise.unit === '';

  async function save() {
    if (!name.trim()) return;
    await db.runAsync('UPDATE exercises SET name = ?, note = ? WHERE id = ?', [name.trim(), note.trim(), exercise.id]);
  }

  async function remove() {
    await db.runAsync('DELETE FROM exercises WHERE id = ?', [exercise.id]);
    router.back();
  }

  return (
    <Screen>
      <Stack.Screen options={{ title: exercise.name }} />
      <Section title={bodyweight ? 'Most reps' : `Heaviest set (${exercise.unit})`}>
        <Card>
          <LineChart
            data={points}
            x="label"
            y={bodyweight ? 'reps' : 'top'}
            names={{ top: 'Heaviest', reps: 'Reps' }}
            title={bodyweight ? 'Most reps per workout' : 'Heaviest set per workout'}
            testID="progress-chart"
          />
        </Card>
      </Section>
      {bodyweight ? null : (
        <Section title="Volume (reps × weight)">
          <Card>
            <BarChart data={points} x="label" y="volume" names={{ volume: 'Volume' }} title="Volume per workout" testID="volume-chart" />
          </Card>
        </Section>
      )}
      <Section title="History">
        <List
          data={[...points].reverse()}
          keyExtractor={(d) => d.workout_id}
          empty={<Text style={{ color: t.colors.textMuted, padding: t.spacing.md }}>Not done yet.</Text>}
          renderItem={(d) => (
            <ListRow
              title={dayLabel(d.day)}
              subtitle={bodyweight ? `best ${d.reps} reps` : `best ${setLabel({ reps: d.reps, weight: d.top }, exercise.unit)}`}
              onPress={() => router.push({ pathname: '/workout/[id]', params: { id: String(d.workout_id) } })}
              testID={`history-${d.workout_id}`}
            />
          )}
        />
      </Section>
      {canEdit ? (
        <View style={{ gap: t.spacing.sm }}>
          <TextField label="Name" value={name} onChangeText={setName} testID="exercise-edit-name" />
          <TextField label="Note" value={note} onChangeText={setNote} multiline placeholder="Form cues, seat height…" testID="exercise-edit-note" />
          <Button title="Save" variant="secondary" onPress={save} testID="exercise-save" />
          <Button title="Delete exercise and its sets" variant="destructive" onPress={remove} testID="exercise-delete" />
        </View>
      ) : null}
    </Screen>
  );
}
