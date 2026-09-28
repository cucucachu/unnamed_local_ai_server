import { Pressable, Text, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useDatabase, useQuery } from '@homeai/sdk';

type Item = { id: number; name: string; done: number };

export default function ItemScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const db = useDatabase();
  const { data } = useQuery<Item>('SELECT id, name, done FROM items WHERE id = ?', [Number(id)]);
  const item = data?.[0];

  async function toggle() {
    await db.runAsync('UPDATE items SET done = 1 - done WHERE id = ?', [Number(id)]);
  }

  return (
    <View style={{ padding: 16, gap: 12 }}>
      <Stack.Screen options={{ title: item ? item.name : 'Item' }} />
      <Text testID="detail-name" style={{ fontSize: 24 }}>{item ? item.name : '…'}</Text>
      <Text testID="detail-status">{item?.done ? 'Done' : 'Not done'}</Text>
      <Pressable testID="toggle" onPress={toggle}>
        <Text style={{ color: '#208AEF' }}>Toggle done</Text>
      </Pressable>
      <Pressable testID="back" onPress={() => router.back()}>
        <Text style={{ color: '#208AEF' }}>Back</Text>
      </Pressable>
    </View>
  );
}
