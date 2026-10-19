import { Text, View } from 'react-native';
import type { IconName } from '@expo/vector-icons';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { runAction, useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { Button, Screen, useTheme } from '@homeai/ui';
import { ItemForm, type ItemValues } from '../../components/ItemForm';
import { ITEM_COLUMNS, statusLabel, today, type Item, type Status } from '../../lib/books';

export default function ItemScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Item>(`SELECT ${ITEM_COLUMNS} FROM items WHERE id = ?`, [Number(id)]);
  const item = data?.[0];

  if (!item) {
    return (
      <Screen>
        <Text testID="item-missing">{loading ? 'Loading…' : 'This was deleted.'}</Text>
      </Screen>
    );
  }

  async function save(v: ItemValues) {
    await db.runAsync('UPDATE items SET title = ?, creator = ?, kind = ?, status = ?, rating = ?, started_on = ?, finished_on = ?, notes = ? WHERE id = ?', [
      v.title,
      v.creator,
      v.kind,
      v.status,
      v.rating,
      v.started_on,
      v.finished_on,
      v.notes,
      item!.id,
    ]);
    router.back();
  }

  async function remove() {
    await db.runAsync('DELETE FROM items WHERE id = ?', [item!.id]);
    router.back();
  }

  const next: { status: Status; title: string; icon: IconName }[] =
    item.status === 'want' ? [{ status: 'started', title: 'Start it today', icon: 'play-outline' }]
    : item.status === 'started' ? [{ status: 'finished', title: 'Finished it today', icon: 'checkmark-outline' }, { status: 'dropped', title: 'Gave up', icon: 'close-outline' }]
    : [];

  return (
    <Screen>
      <Stack.Screen options={{ title: item.title }} />
      <Text style={{ color: t.colors.textMuted }} testID="item-status-label">
        {statusLabel(item)}
      </Text>
      {canEdit && next.length ? (
        <View style={{ flexDirection: 'row', gap: t.spacing.sm }}>
          {next.map((n) => (
            <View key={n.status} style={{ flex: 1 }}>
              <Button title={n.title} variant="secondary" icon={n.icon} onPress={() => void runAction('setStatus', { id: item.id, status: n.status, today: today() })} testID={`item-${n.status}`} />
            </View>
          ))}
        </View>
      ) : null}
      {/* Keyed on the row, so a status change from the buttons above shows in the form. */}
      <ItemForm key={`${item.id}-${item.status}-${item.started_on}-${item.finished_on}`} initial={item} saveTitle="Save" onSave={save} editable={canEdit} />
      {canEdit ? <Button title="Delete" variant="destructive" onPress={remove} testID="item-delete" /> : null}
    </Screen>
  );
}
