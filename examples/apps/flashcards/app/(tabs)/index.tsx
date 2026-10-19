import { useEffect, useState } from 'react';
import { View } from 'react-native';
import { useRouter } from 'expo-router';
import { search, useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { Button, Card, EmptyState, List, ListRow, Screen, SearchField, Section, TextField, useTheme } from '@homeai/ui';
import { CARD_COLUMNS, today, type Card as CardRow } from '../../lib/flashcards';

type DeckRow = { id: number; name: string; description: string; cards: number; due: number };

export default function DecksScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const { data: decks } = useQuery<DeckRow>(
    `SELECT d.id, d.name, d.description, count(c.id) AS cards, count(CASE WHEN c.due <= ? THEN 1 END) AS due
     FROM decks d LEFT JOIN cards c ON c.deck_id = d.id
     GROUP BY d.id ORDER BY d.name COLLATE NOCASE`,
    [today()],
  );
  const due = (decks ?? []).reduce((n, d) => n + d.due, 0);
  const [name, setName] = useState('');
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<number[] | null>(null);
  const { data: hits } = useQuery<CardRow>(`SELECT ${CARD_COLUMNS} FROM cards WHERE id IN (SELECT value FROM json_each(?))`, [JSON.stringify(found ?? [])]);

  // search() finds cards by meaning and by words (homeai.index in app.json); keep the hits' order.
  useEffect(() => {
    if (!query.trim()) return setFound(null);
    let live = true;
    const timer = setTimeout(() => {
      search(query, 30).then(
        (r) => live && setFound(r.filter((h) => h.table === 'cards').map((h) => h.id)),
        () => live && setFound([]),
      );
    }, 300);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [query]);

  async function addDeck() {
    if (!name.trim()) return;
    const { lastInsertRowId } = await db.runAsync('INSERT INTO decks (name) VALUES (?)', [name.trim()]);
    setName('');
    router.push({ pathname: '/deck/[id]', params: { id: String(lastInsertRowId) } });
  }

  const deckName = (id: number) => decks?.find((d) => d.id === id)?.name ?? '';

  return (
    <Screen>
      {decks?.length ? <SearchField value={query} onChangeText={setQuery} placeholder="Search cards" testID="cards-search" /> : null}
      {found ? (
        <Section title="Cards">
          <List
            data={found.map((id) => hits?.find((c) => c.id === id)).filter((c): c is CardRow => !!c)}
            keyExtractor={(c) => c.id}
            empty={<EmptyState icon="search-outline" title="No cards match" testID="search-empty" />}
            renderItem={(c) => (
              <ListRow
                title={c.front}
                subtitle={`${c.back} · ${deckName(c.deck_id)}`}
                onPress={() => router.push({ pathname: '/card/[id]', params: { id: String(c.id) } })}
                testID={`hit-${c.id}`}
              />
            )}
          />
        </Section>
      ) : (
        <>
          {due ? <Button title={`Study all ${due} due`} icon="school-outline" onPress={() => router.push({ pathname: '/study/[deck]', params: { deck: 'all' } })} testID="study-all" /> : null}
          <List
            data={decks ?? []}
            keyExtractor={(d) => d.id}
            empty={<EmptyState icon="layers-outline" title="No decks yet" message="Make a deck below, then add cards to it." testID="decks-empty" />}
            renderItem={(d) => (
              <ListRow
                title={d.name}
                subtitle={`${d.cards} card${d.cards === 1 ? '' : 's'}${d.description ? ` · ${d.description}` : ''}`}
                value={d.due ? `${d.due} due` : undefined}
                onPress={() => router.push({ pathname: '/deck/[id]', params: { id: String(d.id) } })}
                testID={`deck-${d.id}`}
              />
            )}
            testID="decks"
          />
          {canEdit ? (
            <Section title="New deck">
              <Card>
                <View style={{ gap: t.spacing.sm }}>
                  <TextField label="Name" value={name} onChangeText={setName} placeholder="e.g. Spanish verbs" onSubmitEditing={addDeck} testID="deck-name" />
                  <Button title="Add deck" onPress={addDeck} disabled={!name.trim()} testID="deck-add" />
                </View>
              </Card>
            </Section>
          ) : null}
        </>
      )}
    </Screen>
  );
}
