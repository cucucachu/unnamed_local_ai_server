import { useState } from 'react';
import { View } from 'react-native';
import { useRouter } from 'expo-router';
import { useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { Button, Chip, EmptyState, List, ListRow, Screen, Section, TextField, useTheme } from '@homeai/ui';
import { round } from '../../lib/workouts';

type Row = { id: number; name: string; unit: string; best: number | null; sets: number };
const UNITS = [
  { label: 'kg', value: 'kg' },
  { label: 'lb', value: 'lb' },
  { label: 'Bodyweight', value: '' },
];

export default function ExercisesScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const { data } = useQuery<Row>(
    `SELECT e.id, e.name, e.unit, max(s.weight) AS best, count(s.id) AS sets
     FROM exercises e LEFT JOIN sets s ON s.exercise_id = e.id
     GROUP BY e.id ORDER BY e.name COLLATE NOCASE`,
  );
  const [name, setName] = useState('');
  const [unit, setUnit] = useState('kg');

  async function add() {
    if (!name.trim()) return;
    await db.runAsync('INSERT INTO exercises (name, unit) VALUES (?, ?)', [name.trim(), unit]);
    setName('');
  }

  return (
    <Screen>
      <List
        data={data ?? []}
        keyExtractor={(e) => e.id}
        empty={<EmptyState icon="trending-up-outline" title="No exercises yet" testID="exercises-empty" />}
        renderItem={(e) => (
          <ListRow
            title={e.name}
            subtitle={e.sets ? `${e.sets} set${e.sets === 1 ? '' : 's'}${e.best ? ` · best ${round(e.best)} ${e.unit}` : ''}` : 'Not done yet'}
            onPress={() => router.push({ pathname: '/exercise/[id]', params: { id: String(e.id) } })}
            testID={`exercise-${e.id}`}
          />
        )}
        testID="exercises"
      />
      {canEdit ? (
        <Section title="New exercise">
          <View style={{ gap: t.spacing.sm }}>
            <TextField label="Name" value={name} onChangeText={setName} placeholder="e.g. Squat" testID="exercise-name" />
            <View style={{ flexDirection: 'row', gap: t.spacing.sm }}>
              {UNITS.map((u) => (
                <Chip key={u.label} label={u.label} selected={unit === u.value} onPress={() => setUnit(u.value)} testID={`exercise-unit-${u.label}`} />
              ))}
            </View>
            <Button title="Add exercise" variant="secondary" onPress={add} disabled={!name.trim()} testID="exercise-add" />
          </View>
        </Section>
      ) : null}
    </Screen>
  );
}
