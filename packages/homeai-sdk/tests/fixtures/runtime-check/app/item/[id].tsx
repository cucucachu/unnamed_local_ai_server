import { useEffect, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useDatabase, useQuery } from '@homeai/sdk';

type Item = { id: number; name: string; done: number };

export default function ItemScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const db = useDatabase();
  const [name, setName] = useState<string | null>(null);
  const { data } = useQuery<{ done: number }>('SELECT done FROM items WHERE id = $id', { $id: Number(id) });

  useEffect(() => {
    db.getFirstAsync<Item>('SELECT id, name, done FROM items WHERE id = ?', Number(id)).then((row) => setName(row ? row.name : 'missing'));
  }, [db, id]);

  return (
    <View style={{ padding: 16, gap: 8 }}>
      <Stack.Screen options={{ title: name ?? 'Item' }} />
      <Text testID="detail-name">{name ?? '…'}</Text>
      <Text testID="detail-status">{data?.[0]?.done ? 'Done' : 'Not done'}</Text>
      <Pressable testID="toggle" onPress={() => db.runAsync('UPDATE items SET done = 1 - done WHERE id = ?', Number(id))}>
        <Text>Toggle done</Text>
      </Pressable>
      <Pressable testID="back" onPress={() => router.back()}>
        <Text>Back</Text>
      </Pressable>
    </View>
  );
}
