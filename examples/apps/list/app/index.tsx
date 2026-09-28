import { useState } from 'react';
import { FlatList, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { Link } from 'expo-router';
import { runAction, useDatabase, useQuery, useSpace } from '@homeai/sdk';

type Item = { id: number; name: string; done: number };

export default function ListScreen() {
  const db = useDatabase();
  const space = useSpace();
  const canEdit = space?.role !== 'viewer';
  const { data: items, loading, error } = useQuery<Item>(
    'SELECT id, name, done FROM items ORDER BY done, id',
  );
  const [name, setName] = useState('');
  const [message, setMessage] = useState('');

  const doneCount = (items ?? []).filter((item) => item.done).length;
  const openCount = (items ?? []).length - doneCount;

  async function addItem() {
    if (!name.trim()) return;
    try {
      await runAction('addItem', { name });
      setName('');
      setMessage('');
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function setDone(item: Item, done: boolean) {
    try {
      await db.runAsync('UPDATE items SET done = ? WHERE id = ?', [done ? 1 : 0, item.id]);
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function clearDone() {
    try {
      const result = await runAction('clearDone');
      setMessage(`Cleared ${result.changes} done item${result.changes === 1 ? '' : 's'}`);
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  return (
    <View style={styles.screen}>
      {canEdit ? (
        <View style={styles.addRow}>
          <TextInput
            testID="new-item-name"
            style={styles.input}
            value={name}
            onChangeText={setName}
            onSubmitEditing={addItem}
            placeholder="Add an item"
            returnKeyType="done"
          />
          <Pressable testID="add-item" style={styles.button} onPress={addItem}>
            <Text style={styles.buttonText}>Add</Text>
          </Pressable>
        </View>
      ) : (
        <Text testID="view-only" style={styles.muted}>
          View only
        </Text>
      )}

      <Text testID="summary" style={styles.muted}>
        {openCount} open, {doneCount} done
      </Text>
      {loading && <Text>Loading…</Text>}
      {error && <Text style={styles.error}>{error.message}</Text>}
      {message !== '' && (
        <Text testID="message" style={styles.muted}>
          {message}
        </Text>
      )}

      <FlatList
        data={items ?? []}
        keyExtractor={(item) => String(item.id)}
        ListEmptyComponent={loading ? null : <Text style={styles.muted}>Nothing on the list.</Text>}
        renderItem={({ item }) => (
          <View style={styles.itemRow}>
            <Pressable
              testID={`check-${item.id}`}
              role="checkbox"
              aria-checked={item.done === 1}
              aria-label={item.name}
              disabled={!canEdit}
              onPress={() => setDone(item, !item.done)}
              style={[styles.checkbox, item.done ? styles.checkboxChecked : null]}
            >
              <Text style={styles.checkmark}>{item.done ? '✓' : ''}</Text>
            </Pressable>
            <Link href={{ pathname: '/item/[id]', params: { id: String(item.id) } }} testID={`item-${item.id}`} style={styles.itemName}>
              <Text style={item.done ? styles.checkedText : null}>{item.name}</Text>
            </Link>
          </View>
        )}
      />

      {canEdit && (
        <Pressable
          testID="clear-done"
          style={[styles.button, doneCount === 0 ? styles.buttonDisabled : null]}
          disabled={doneCount === 0}
          onPress={clearDone}
        >
          <Text style={styles.buttonText}>Clear done ({doneCount})</Text>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, padding: 16, gap: 12 },
  addRow: { flexDirection: 'row', gap: 8 },
  input: { flex: 1, borderWidth: 1, borderColor: '#999', borderRadius: 6, padding: 8, fontSize: 16 },
  button: { backgroundColor: '#208AEF', borderRadius: 6, paddingHorizontal: 16, paddingVertical: 10, alignItems: 'center' },
  buttonDisabled: { opacity: 0.4 },
  buttonText: { color: 'white', fontWeight: '600' },
  itemRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 8 },
  checkbox: { width: 24, height: 24, borderWidth: 2, borderColor: '#208AEF', borderRadius: 4, alignItems: 'center', justifyContent: 'center' },
  checkboxChecked: { backgroundColor: '#208AEF' },
  checkmark: { color: 'white', fontWeight: '700' },
  itemName: { flex: 1, fontSize: 18 },
  checkedText: { textDecorationLine: 'line-through', color: '#888' },
  muted: { color: '#666' },
  error: { color: 'crimson' },
});
