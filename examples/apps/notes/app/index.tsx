import { useState } from 'react';
import { FlatList, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { Link } from 'expo-router';
import { runAction, useQuery, useSpace } from '@homeai/sdk';

type Note = { id: number; title: string; body: string; pinned: number };

export default function NotesScreen() {
  const space = useSpace();
  const canEdit = space?.role !== 'viewer';
  const { data: notes, loading, error } = useQuery<Note>(
    'SELECT id, title, body, pinned FROM notes ORDER BY pinned DESC, updated_at DESC',
  );
  const [title, setTitle] = useState('');
  const [message, setMessage] = useState('');

  async function addNote() {
    if (!title.trim()) return;
    try {
      await runAction('addNote', { title });
      setTitle('');
      setMessage('');
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  return (
    <View style={styles.screen}>
      {canEdit ? (
        <View style={styles.addRow}>
          <TextInput
            testID="new-note-title"
            style={styles.input}
            value={title}
            onChangeText={setTitle}
            onSubmitEditing={addNote}
            placeholder="New note title"
            returnKeyType="done"
          />
          <Pressable testID="add-note" style={styles.button} onPress={addNote}>
            <Text style={styles.buttonText}>Add</Text>
          </Pressable>
        </View>
      ) : (
        <Text testID="view-only" style={styles.muted}>
          View only
        </Text>
      )}

      {loading && <Text>Loading…</Text>}
      {error && <Text style={styles.error}>{error.message}</Text>}
      {message !== '' && (
        <Text testID="message" style={styles.muted}>
          {message}
        </Text>
      )}

      <FlatList
        data={notes ?? []}
        keyExtractor={(note) => String(note.id)}
        ListEmptyComponent={loading ? null : <Text style={styles.muted}>No notes yet.</Text>}
        renderItem={({ item }) => (
          <Link href={{ pathname: '/note/[id]', params: { id: String(item.id) } }} testID={`note-${item.id}`} style={styles.item}>
            <Text style={styles.title}>
              {item.pinned ? '📌 ' : ''}
              {item.title}
            </Text>
            {item.body !== '' && (
              <Text style={styles.muted} numberOfLines={2}>
                {item.body}
              </Text>
            )}
          </Link>
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
  item: { paddingVertical: 10 },
  title: { fontSize: 18, fontWeight: '600' },
  muted: { color: '#666' },
  error: { color: 'crimson' },
});
