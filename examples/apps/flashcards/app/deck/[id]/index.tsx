import { useState } from 'react';
import { Text, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { Button, Card, EmptyState, IconButton, List, ListRow, Screen, Section, TextField, useTheme } from '@homeai/ui';
import { CARD_COLUMNS, dueLabel, today, type Card as CardRow, type Deck } from '../../../lib/flashcards';

export default function DeckScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Deck>('SELECT id, name, description FROM decks WHERE id = ?', [Number(id)]);
  const deck = data?.[0];
  const { data: cards } = useQuery<CardRow>(`SELECT ${CARD_COLUMNS} FROM cards WHERE deck_id = ? ORDER BY due, id`, [Number(id)]);
  const [front, setFront] = useState('');
  const [back, setBack] = useState('');
  const [message, setMessage] = useState('');

  if (!deck) {
    return (
      <Screen>
        <Text testID="deck-missing">{loading ? 'Loading…' : 'This deck was deleted.'}</Text>
      </Screen>
    );
  }
  const now = today();
  const due = (cards ?? []).filter((c) => c.due <= now).length;

  async function addCard() {
    if (!front.trim()) return setMessage('Write the front of the card.');
    setMessage('');
    await db.runAsync('INSERT INTO cards (deck_id, front, back, due) VALUES (?, ?, ?, ?)', [deck!.id, front.trim(), back.trim(), today()]);
    setFront('');
    setBack('');
  }

  return (
    <Screen>
      <Stack.Screen
        options={{
          title: deck.name,
          headerRight: () =>
            canEdit ? <IconButton icon="create-outline" accessibilityLabel="Edit deck" onPress={() => router.push({ pathname: '/deck/[id]/edit', params: { id: String(deck.id) } })} testID="deck-edit" /> : null,
        }}
      />
      {deck.description ? <Text style={{ color: t.colors.textMuted }}>{deck.description}</Text> : null}
      <Button
        title={due ? `Study ${due} due` : 'Nothing due today'}
        icon="school-outline"
        disabled={!due}
        onPress={() => router.push({ pathname: '/study/[deck]', params: { deck: String(deck.id) } })}
        testID="deck-study"
      />
      {canEdit ? (
        <Section title="Add a card">
          <Card>
            <View style={{ gap: t.spacing.sm }}>
              <TextField label="Front" value={front} onChangeText={setFront} placeholder="The question or word" multiline testID="card-front" />
              <TextField label="Back" value={back} onChangeText={setBack} placeholder="The answer" multiline testID="card-back" />
              {message ? (
                <Text role="alert" style={{ color: t.colors.danger }}>
                  {message}
                </Text>
              ) : null}
              <Button title="Add card" onPress={addCard} testID="card-add" />
            </View>
          </Card>
        </Section>
      ) : null}
      <Section title={`${cards?.length ?? 0} card${cards?.length === 1 ? '' : 's'}`}>
        <List
          data={cards ?? []}
          keyExtractor={(c) => c.id}
          empty={<EmptyState icon="albums-outline" title="No cards yet" testID="cards-empty" />}
          renderItem={(c) => (
            <ListRow
              title={c.front}
              subtitle={c.back || undefined}
              value={dueLabel(c)}
              onPress={() => router.push({ pathname: '/card/[id]', params: { id: String(c.id) } })}
              testID={`card-${c.id}`}
            />
          )}
          testID="cards"
        />
      </Section>
    </Screen>
  );
}
