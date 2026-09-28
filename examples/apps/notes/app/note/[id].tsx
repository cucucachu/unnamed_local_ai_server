import { useState } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useDatabase, useQuery, useSpace } from '@homeai/sdk';

type Note = { id: number; title: string; body: string; pinned: number; created_at: string; updated_at: string };

export default function NoteScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Note>(
    'SELECT id, title, body, pinned, created_at, updated_at FROM notes WHERE id = ?',
    [Number(id)],
  );
  const note = data?.[0];

  if (!note) {
    return (
      <View style={styles.screen}>
        <Stack.Screen options={{ title: 'Note' }} />
        <Text testID="detail-missing">{loading ? 'Loading…' : 'This note is no longer here.'}</Text>
      </View>
    );
  }
  return <NoteForm key={note.id} note={note} />;
}

function NoteForm({ note }: { note: Note }) {
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const [title, setTitle] = useState(note.title);
  const [body, setBody] = useState(note.body);
  const [message, setMessage] = useState('');

  async function save() {
    if (!title.trim()) {
      setMessage("The title can't be empty.");
      return;
    }
    try {
      await db.runAsync(
        "UPDATE notes SET title = ?, body = ?, updated_at = datetime('now') WHERE id = ?",
        [title.trim(), body, note.id],
      );
      router.back();
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function togglePinned() {
    try {
      await db.runAsync(
        "UPDATE notes SET pinned = ?, updated_at = datetime('now') WHERE id = ?",
        [note.pinned ? 0 : 1, note.id],
      );
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function remove() {
    try {
      await db.runAsync('DELETE FROM notes WHERE id = ?', [note.id]);
      router.back();
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  return (
    <View style={styles.screen}>
      <Stack.Screen options={{ title: note.title }} />

      <Text style={styles.label}>Title</Text>
      <TextInput testID="detail-title" style={styles.input} value={title} onChangeText={setTitle} editable={canEdit} />
      <Text style={styles.label}>Body</Text>
      <TextInput
        testID="detail-body"
        style={[styles.input, styles.body]}
        value={body}
        onChangeText={setBody}
        placeholder="Write the note…"
        multiline
        editable={canEdit}
      />

      <Text testID="detail-status">{note.pinned ? 'Pinned' : 'Not pinned'}</Text>
      <Text style={styles.muted}>Updated {note.updated_at} UTC</Text>
      {message !== '' && <Text style={styles.error}>{message}</Text>}

      {canEdit && (
        <View style={styles.buttons}>
          <Pressable testID="detail-save" style={styles.button} onPress={save}>
            <Text style={styles.buttonText}>Save</Text>
          </Pressable>
          <Pressable testID="detail-pin" style={styles.secondaryButton} onPress={togglePinned}>
            <Text style={styles.secondaryButtonText}>{note.pinned ? 'Unpin' : 'Pin'}</Text>
          </Pressable>
          <Pressable testID="detail-delete" style={styles.secondaryButton} onPress={remove}>
            <Text style={styles.deleteText}>Delete</Text>
          </Pressable>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, padding: 16, gap: 8 },
  label: { fontWeight: '600', marginTop: 8 },
  input: { borderWidth: 1, borderColor: '#999', borderRadius: 6, padding: 8, fontSize: 16 },
  body: { minHeight: 160, textAlignVertical: 'top' },
  buttons: { flexDirection: 'row', gap: 8, marginTop: 16 },
  button: { backgroundColor: '#208AEF', borderRadius: 6, paddingHorizontal: 16, paddingVertical: 10 },
  buttonText: { color: 'white', fontWeight: '600' },
  secondaryButton: { borderWidth: 1, borderColor: '#999', borderRadius: 6, paddingHorizontal: 16, paddingVertical: 10 },
  secondaryButtonText: { color: '#208AEF', fontWeight: '600' },
  deleteText: { color: 'crimson', fontWeight: '600' },
  muted: { color: '#666' },
  error: { color: 'crimson' },
});
