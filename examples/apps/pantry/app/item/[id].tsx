import { Text, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { runAction, useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { Button, Screen, useTheme } from '@homeai/ui';
import { ItemForm, type ItemValues } from '../../components/ItemForm';
import { ITEM_COLUMNS, amount, expiryLabel, type Item } from '../../lib/pantry';

export default function ItemScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Item>(`SELECT ${ITEM_COLUMNS} FROM items WHERE id = ?`, [Number(id)]);
  const item = data?.[0];
  if (!item) {
    return (
      <Screen>
        <Text testID="item-missing">{loading ? 'Loading…' : 'This is used up or was deleted.'}</Text>
      </Screen>
    );
  }
  // `key` gives the form fresh state when the quantity changes underneath it.
  return <ItemDetail key={`${item.id}:${item.quantity}`} item={item} />;
}

function ItemDetail({ item }: { item: Item }) {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';

  async function save(v: ItemValues) {
    await db.runAsync('UPDATE items SET name = ?, quantity = ?, unit = ?, location = ?, expires_on = ?, note = ? WHERE id = ?', [
      v.name,
      v.quantity,
      v.unit,
      v.location,
      v.expires_on,
      v.note,
      item.id,
    ]);
    router.back();
  }

  async function useOne() {
    const { changes } = await runAction('useSome', { id: item.id, amount: 1 });
    if (changes > 1) router.back();
  }

  async function remove() {
    await db.runAsync('DELETE FROM items WHERE id = ?', [item.id]);
    router.back();
  }

  return (
    <Screen>
      <Stack.Screen options={{ title: item.name }} />
      <Text style={{ color: t.colors.textMuted }} testID="item-status">
        {[`${amount(item)} in the ${item.location}`, expiryLabel(item)].filter(Boolean).join(' · ')}
      </Text>
      {canEdit ? (
        <View style={{ flexDirection: 'row', gap: t.spacing.sm }}>
          <Button title={item.quantity > 1 ? 'Use 1' : 'Use it up'} variant="secondary" icon="remove-outline" onPress={useOne} testID="item-use" />
          <Button title="Delete" variant="destructive" onPress={remove} testID="item-delete" />
        </View>
      ) : null}
      <ItemForm initial={item} saveTitle="Save" onSave={save} editable={canEdit} />
    </Screen>
  );
}
