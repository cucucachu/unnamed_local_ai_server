import { useMemo, useState } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { useTheme } from '@homeai/ui';

type Item = { id: number; name: string; quantity: string; note: string; checked: number; created_at: string };

export default function ItemScreen() {
  const styles = useStyles();
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Item>(
    'SELECT id, name, quantity, note, checked, created_at FROM items WHERE id = ?',
    [Number(id)],
  );
  const item = data?.[0];

  if (!item) {
    return (
      <View style={styles.screen}>
        <Stack.Screen options={{ title: 'Item' }} />
        <Text testID="detail-missing">{loading ? 'Loading…' : 'This item is no longer on the list.'}</Text>
      </View>
    );
  }
  // `key` gives the form fresh state if the route switches to another item.
  return <ItemForm key={item.id} item={item} />;
}

function ItemForm({ item }: { item: Item }) {
  const styles = useStyles();
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const [name, setName] = useState(item.name);
  const [quantity, setQuantity] = useState(item.quantity);
  const [note, setNote] = useState(item.note);
  const [message, setMessage] = useState('');

  async function save() {
    if (!name.trim()) {
      setMessage('The name can’t be empty.');
      return;
    }
    try {
      await db.runAsync('UPDATE items SET name = ?, quantity = ?, note = ? WHERE id = ?', [
        name.trim(),
        quantity.trim(),
        note.trim(),
        item.id,
      ]);
      router.back();
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function toggleChecked() {
    try {
      await db.runAsync('UPDATE items SET checked = ? WHERE id = ?', [item.checked ? 0 : 1, item.id]);
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function remove() {
    try {
      await db.runAsync('DELETE FROM items WHERE id = ?', [item.id]);
      router.back();
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  return (
    <View style={styles.screen}>
      <Stack.Screen options={{ title: item.name }} />

      <Text style={styles.label}>Name</Text>
      <TextInput testID="detail-name" style={styles.input} value={name} onChangeText={setName} editable={canEdit} />
      <Text style={styles.label}>Quantity</Text>
      <TextInput
        testID="detail-quantity"
        style={styles.input}
        value={quantity}
        onChangeText={setQuantity}
        placeholder="e.g. 2, 1 lb"
        editable={canEdit}
      />
      <Text style={styles.label}>Note</Text>
      <TextInput
        testID="detail-note"
        style={[styles.input, styles.note]}
        value={note}
        onChangeText={setNote}
        placeholder="Brand, size, where to find it…"
        multiline
        editable={canEdit}
      />

      <Text testID="detail-status">{item.checked ? 'Checked' : 'Not checked'}</Text>
      <Text style={styles.muted}>Added {item.created_at} UTC</Text>
      {message !== '' && <Text style={styles.error}>{message}</Text>}

      {canEdit && (
        <View style={styles.buttons}>
          <Pressable testID="detail-save" style={styles.button} onPress={save}>
            <Text style={styles.buttonText}>Save</Text>
          </Pressable>
          <Pressable testID="detail-toggle" style={styles.secondaryButton} onPress={toggleChecked}>
            <Text style={styles.secondaryButtonText}>{item.checked ? 'Uncheck' : 'Check off'}</Text>
          </Pressable>
          <Pressable testID="detail-delete" style={styles.secondaryButton} onPress={remove}>
            <Text style={styles.deleteText}>Delete</Text>
          </Pressable>
        </View>
      )}
    </View>
  );
}

// Colors come from the theme, so the app follows the device's light/dark setting.
function useStyles() {
  const t = useTheme();
  return useMemo(
    () =>
      StyleSheet.create({
        screen: { flex: 1, padding: 16, gap: 8 },
        label: { fontWeight: '600', marginTop: 8 },
        input: { borderWidth: 1, borderColor: t.colors.border, borderRadius: 6, padding: 8, fontSize: 16 },
        note: { minHeight: 80, textAlignVertical: 'top' },
        buttons: { flexDirection: 'row', gap: 8, marginTop: 16 },
        button: { backgroundColor: t.colors.tint, borderRadius: 6, paddingHorizontal: 16, paddingVertical: 10 },
        buttonText: { color: t.colors.onTint, fontWeight: '600' },
        secondaryButton: { borderWidth: 1, borderColor: t.colors.border, borderRadius: 6, paddingHorizontal: 16, paddingVertical: 10 },
        secondaryButtonText: { color: t.colors.tint, fontWeight: '600' },
        deleteText: { color: t.colors.danger, fontWeight: '600' },
        muted: { color: t.colors.textMuted },
        error: { color: t.colors.danger },
      }),
    [t],
  );
}
