import { useState } from 'react';
import { FlatList, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { Link } from 'expo-router';
import { runAction, useDatabase, useQuery, useSpace } from '@homeai/sdk';

type Habit = { id: number; name: string; done_today: number };

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function TrackerScreen() {
  const db = useDatabase();
  const space = useSpace();
  const canEdit = space?.role !== 'viewer';
  const { data: habits, loading, error } = useQuery<Habit>(
    "SELECT h.id, h.name, CASE WHEN c.id IS NULL THEN 0 ELSE 1 END AS done_today FROM habits h LEFT JOIN checkins c ON c.habit_id = h.id AND c.day = date('now') ORDER BY h.id",
  );
  const [name, setName] = useState('');
  const [message, setMessage] = useState('');

  async function addHabit() {
    if (!name.trim()) return;
    try {
      await runAction('addHabit', { name });
      setName('');
      setMessage('');
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function toggleToday(habit: Habit) {
    try {
      if (habit.done_today) {
        await db.runAsync('DELETE FROM checkins WHERE habit_id = ? AND day = date(\'now\')', [habit.id]);
      } else {
        await runAction('checkIn', { habit_id: habit.id, day: today() });
      }
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  const doneCount = (habits ?? []).filter((h) => h.done_today).length;

  return (
    <View style={styles.screen}>
      {canEdit ? (
        <View style={styles.addRow}>
          <TextInput
            testID="new-habit-name"
            style={styles.input}
            value={name}
            onChangeText={setName}
            onSubmitEditing={addHabit}
            placeholder="Add a habit"
            returnKeyType="done"
          />
          <Pressable testID="add-habit" style={styles.button} onPress={addHabit}>
            <Text style={styles.buttonText}>Add</Text>
          </Pressable>
        </View>
      ) : (
        <Text testID="view-only" style={styles.muted}>
          View only
        </Text>
      )}

      <Text testID="summary" style={styles.muted}>
        {doneCount} of {(habits ?? []).length} today
      </Text>
      {loading && <Text>Loading…</Text>}
      {error && <Text style={styles.error}>{error.message}</Text>}
      {message !== '' && (
        <Text testID="message" style={styles.muted}>
          {message}
        </Text>
      )}

      <FlatList
        data={habits ?? []}
        keyExtractor={(habit) => String(habit.id)}
        ListEmptyComponent={loading ? null : <Text style={styles.muted}>No habits yet.</Text>}
        renderItem={({ item }) => (
          <View style={styles.itemRow}>
            <Pressable
              testID={`check-${item.id}`}
              role="checkbox"
              aria-checked={item.done_today === 1}
              aria-label={item.name}
              disabled={!canEdit}
              onPress={() => toggleToday(item)}
              style={[styles.checkbox, item.done_today ? styles.checkboxChecked : null]}
            >
              <Text style={styles.checkmark}>{item.done_today ? '✓' : ''}</Text>
            </Pressable>
            <Link href={{ pathname: '/habit/[id]', params: { id: String(item.id) } }} testID={`habit-${item.id}`} style={styles.itemName}>
              <Text>{item.name}</Text>
            </Link>
          </View>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, padding: 16, gap: 12 },
  addRow: { flexDirection: 'row', gap: 8 },
  input: { flex: 1, borderWidth: 1, borderColor: '#999', borderRadius: 6, padding: 8, fontSize: 16 },
  button: { backgroundColor: '#208AEF', borderRadius: 6, paddingHorizontal: 16, paddingVertical: 10, alignItems: 'center' },
  buttonText: { color: 'white', fontWeight: '600' },
  itemRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 8 },
  checkbox: { width: 24, height: 24, borderWidth: 2, borderColor: '#208AEF', borderRadius: 4, alignItems: 'center', justifyContent: 'center' },
  checkboxChecked: { backgroundColor: '#208AEF' },
  checkmark: { color: 'white', fontWeight: '700' },
  itemName: { flex: 1, fontSize: 18 },
  muted: { color: '#666' },
  error: { color: 'crimson' },
});
