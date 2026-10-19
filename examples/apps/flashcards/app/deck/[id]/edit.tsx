import { useState } from 'react';
import { Text } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useDatabase, useQuery } from '@homeai/sdk';
import { Button, Screen, TextField } from '@homeai/ui';
import type { Deck } from '../../../lib/flashcards';

export default function EditDeckScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Deck>('SELECT id, name, description FROM decks WHERE id = ?', [Number(id)]);
  const deck = data?.[0];
  if (!deck) {
    return (
      <Screen>
        <Text testID="deck-missing">{loading ? 'Loading…' : 'This deck was deleted.'}</Text>
      </Screen>
    );
  }
  return <DeckForm key={deck.id} deck={deck} />;
}

function DeckForm({ deck }: { deck: Deck }) {
  const db = useDatabase();
  const router = useRouter();
  const [name, setName] = useState(deck.name);
  const [description, setDescription] = useState(deck.description);

  async function save() {
    if (!name.trim()) return;
    await db.runAsync('UPDATE decks SET name = ?, description = ? WHERE id = ?', [name.trim(), description.trim(), deck.id]);
    router.back();
  }

  async function remove() {
    await db.runAsync('DELETE FROM decks WHERE id = ?', [deck.id]);
    router.dismissAll();
  }

  return (
    <Screen>
      <TextField label="Name" value={name} onChangeText={setName} testID="deck-edit-name" />
      <TextField label="Description" value={description} onChangeText={setDescription} placeholder="What it's for" multiline testID="deck-edit-description" />
      <Button title="Save" onPress={save} disabled={!name.trim()} testID="deck-save" />
      <Button title="Delete deck and its cards" variant="destructive" onPress={remove} testID="deck-delete" />
    </Screen>
  );
}
