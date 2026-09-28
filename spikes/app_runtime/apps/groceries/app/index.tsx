import { useState } from 'react';
import { FlatList, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { Link } from 'expo-router';
import { useDatabase, useQuery } from '@homeai/sdk';

type Item = { id: number; name: string; done: number };

export default function Index() {
  const db = useDatabase();
  const { data, loading, error } = useQuery<Item>('SELECT id, name, done FROM items ORDER BY id');
  const [name, setName] = useState('');

  async function add() {
    if (!name.trim()) return;
    await db.runAsync('INSERT INTO items (name) VALUES (?)', [name.trim()]);
    setName('');
  }

  return (
    <View style={styles.page}>
      <View style={styles.row}>
        <TextInput testID="new-item" style={styles.input} value={name} onChangeText={setName} placeholder="Add an item" />
        <Pressable testID="add" onPress={add} style={styles.button}>
          <Text style={styles.buttonText}>Add</Text>
        </Pressable>
      </View>
      {loading && <Text>Loading…</Text>}
      {error && <Text style={styles.error}>{String(error.message)}</Text>}
      <FlatList
        data={data ?? []}
        keyExtractor={(item) => String(item.id)}
        renderItem={({ item }) => (
          <Link href={{ pathname: '/item/[id]', params: { id: String(item.id) } }} testID={`item-${item.id}`}>
            <Text style={[styles.item, item.done ? styles.done : null]}>{item.name}</Text>
          </Link>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1, padding: 16, gap: 12 },
  row: { flexDirection: 'row', gap: 8 },
  input: { flex: 1, borderWidth: 1, borderColor: '#999', padding: 8, borderRadius: 6 },
  button: { backgroundColor: '#208AEF', paddingHorizontal: 16, justifyContent: 'center', borderRadius: 6 },
  buttonText: { color: 'white', fontWeight: '600' },
  item: { paddingVertical: 10, fontSize: 18 },
  done: { textDecorationLine: 'line-through', color: '#888' },
  error: { color: 'crimson' },
});
