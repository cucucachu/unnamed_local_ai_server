import { useState } from 'react';
import { Pressable, Text, TextInput, View } from 'react-native';
import { Link } from 'expo-router';
import { askAgent, runAction, useDatabase, useQuery, useSpace, type SDKError } from '@homeai/sdk';

type Item = { id: number; name: string; done: number };

export const BUILD = 'v1';

export default function Index() {
  const db = useDatabase();
  const space = useSpace();
  const { data, error } = useQuery<Item>('SELECT id, name, done FROM items ORDER BY id');
  const [name, setName] = useState('');
  const [status, setStatus] = useState('');

  async function add() {
    if (!name.trim()) return;
    try {
      const r = await db.runAsync('INSERT INTO items (name) VALUES (?)', [name.trim()]);
      setStatus(`added ${r.lastInsertRowId}`);
      setName('');
    } catch (e) {
      setStatus(`refused: ${(e as SDKError).code}`);
    }
  }

  async function markAll() {
    const r = await runAction('markAllDone');
    setStatus(`${r.changes} marked, ${r.rows[0]?.n} done`);
  }

  return (
    <View style={{ flex: 1, padding: 16, gap: 8 }}>
      <Text testID="build">{BUILD}</Text>
      <Text testID="space">{space ? `${space.name} (${space.role})` : 'no space'}</Text>
      <TextInput testID="new-item" value={name} onChangeText={setName} placeholder="Add an item" />
      <Pressable testID="add" onPress={add}>
        <Text>Add</Text>
      </Pressable>
      <Pressable testID="mark-all" onPress={markAll}>
        <Text>Mark all done</Text>
      </Pressable>
      <Pressable testID="ask-agent" onPress={() => void askAgent('Add Milk via app_sql').catch((e) => setStatus(`refused: ${(e as SDKError).code}`))}>
        <Text>Ask the agent</Text>
      </Pressable>
      <Text testID="status">{status}</Text>
      {error && <Text testID="error">{error.message}</Text>}
      <Text testID="count">{data ? `${data.length} items` : 'loading'}</Text>
      {(data ?? []).map((item) => (
        <Link key={item.id} href={{ pathname: '/item/[id]', params: { id: String(item.id) } }} testID={`item-${item.id}`}>
          {item.name}
          {item.done ? ' ✓' : ''}
        </Link>
      ))}
    </View>
  );
}
