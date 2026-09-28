import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { runAction, useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { useState } from 'react';

type Habit = { id: number; name: string; created_at: string };
type Checkin = { id: number; day: string };

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function HabitScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Habit>('SELECT id, name, created_at FROM habits WHERE id = ?', [
    Number(id),
  ]);
  const habit = data?.[0];

  if (!habit) {
    return (
      <View style={styles.screen}>
        <Stack.Screen options={{ title: 'Habit' }} />
        <Text testID="detail-missing">{loading ? 'Loading…' : 'This habit is no longer here.'}</Text>
      </View>
    );
  }
  return <HabitDetail key={habit.id} habit={habit} />;
}

function HabitDetail({ habit }: { habit: Habit }) {
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const { data: checkins } = useQuery<Checkin>(
    'SELECT id, day FROM checkins WHERE habit_id = ? ORDER BY day DESC',
    [habit.id],
  );
  const [message, setMessage] = useState('');
  const doneToday = (checkins ?? []).some((c) => c.day === today());

  async function checkIn() {
    try {
      await runAction('checkIn', { habit_id: habit.id, day: today() });
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function undoToday() {
    try {
      await db.runAsync('DELETE FROM checkins WHERE habit_id = ? AND day = ?', [habit.id, today()]);
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function remove() {
    try {
      await db.runAsync('DELETE FROM habits WHERE id = ?', [habit.id]);
      router.back();
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  return (
    <View style={styles.screen}>
      <Stack.Screen options={{ title: habit.name }} />
      <Text testID="detail-status">{doneToday ? 'Done today' : 'Not yet today'}</Text>
      <Text style={styles.muted}>{(checkins ?? []).length} check-in{(checkins ?? []).length === 1 ? '' : 's'}</Text>
      {message !== '' && <Text style={styles.error}>{message}</Text>}

      {(checkins ?? []).map((c) => (
        <Text key={c.id} testID={`day-${c.day}`} style={styles.day}>
          {c.day}
        </Text>
      ))}
      {!checkins?.length && <Text style={styles.muted}>No check-ins yet.</Text>}

      {canEdit && (
        <View style={styles.buttons}>
          {doneToday ? (
            <Pressable testID="detail-undo" style={styles.secondaryButton} onPress={undoToday}>
              <Text style={styles.secondaryButtonText}>Undo today</Text>
            </Pressable>
          ) : (
            <Pressable testID="detail-checkin" style={styles.button} onPress={checkIn}>
              <Text style={styles.buttonText}>Check in</Text>
            </Pressable>
          )}
          <Pressable testID="detail-delete" style={styles.secondaryButton} onPress={remove}>
            <Text style={styles.deleteText}>Delete habit</Text>
          </Pressable>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, padding: 16, gap: 8 },
  buttons: { flexDirection: 'row', gap: 8, marginTop: 16 },
  button: { backgroundColor: '#208AEF', borderRadius: 6, paddingHorizontal: 16, paddingVertical: 10 },
  buttonText: { color: 'white', fontWeight: '600' },
  secondaryButton: { borderWidth: 1, borderColor: '#999', borderRadius: 6, paddingHorizontal: 16, paddingVertical: 10 },
  secondaryButtonText: { color: '#208AEF', fontWeight: '600' },
  deleteText: { color: 'crimson', fontWeight: '600' },
  day: { fontSize: 16 },
  muted: { color: '#666' },
  error: { color: 'crimson' },
});
