import { useState } from 'react';
import { Text } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { runAction, useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { Button, List, ListRow, Screen, Section, Select, TextField } from '@homeai/ui';
import { CARD_COLUMNS, GRADES, dueLabel, intervalLabel, today, type Card, type Deck } from '../../lib/flashcards';

export default function CardScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Card>(`SELECT ${CARD_COLUMNS} FROM cards WHERE id = ?`, [Number(id)]);
  const card = data?.[0];
  if (!card) {
    return (
      <Screen>
        <Text testID="card-missing">{loading ? 'Loading…' : 'This card was deleted.'}</Text>
      </Screen>
    );
  }
  return <CardDetail key={card.id} card={card} />;
}

function CardDetail({ card }: { card: Card }) {
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const { data: decks } = useQuery<Deck>('SELECT id, name, description FROM decks ORDER BY name COLLATE NOCASE');
  const { data: reviews } = useQuery<{ id: number; day: string; grade: number }>('SELECT id, day, grade FROM reviews WHERE card_id = ? ORDER BY id DESC LIMIT 20', [card.id]);
  const [front, setFront] = useState(card.front);
  const [back, setBack] = useState(card.back);
  const [deck, setDeck] = useState<number | null>(card.deck_id);

  async function save() {
    if (!front.trim() || deck === null) return;
    await db.runAsync('UPDATE cards SET front = ?, back = ?, deck_id = ? WHERE id = ?', [front.trim(), back.trim(), deck, card.id]);
    router.back();
  }

  async function remove() {
    await db.runAsync('DELETE FROM cards WHERE id = ?', [card.id]);
    router.back();
  }

  const schedule =
    card.reps === 0 && card.interval_days === 0
      ? `${dueLabel(card)}. Ease ${card.ease.toFixed(2)}.`
      : `${dueLabel(card)}, every ${intervalLabel(card.interval_days)} now. Ease ${card.ease.toFixed(2)}, forgotten ${card.lapses} time${card.lapses === 1 ? '' : 's'}.`;

  return (
    <Screen>
      <TextField label="Front" value={front} onChangeText={setFront} multiline editable={canEdit} testID="card-edit-front" />
      <TextField label="Back" value={back} onChangeText={setBack} multiline editable={canEdit} testID="card-edit-back" />
      {canEdit ? <Select label="Deck" value={deck} options={(decks ?? []).map((d) => ({ label: d.name, value: d.id }))} onChange={setDeck} testID="card-deck" /> : null}
      <Text testID="card-schedule">{schedule}</Text>
      {canEdit ? (
        <>
          <Button title="Save" onPress={save} disabled={!front.trim()} testID="card-save" />
          <Button title="Start this card over" variant="secondary" icon="refresh-outline" onPress={() => void runAction('resetCard', { card_id: card.id, today: today() })} testID="card-reset" />
          <Button title="Delete card" variant="destructive" onPress={remove} testID="card-delete" />
        </>
      ) : null}
      {reviews?.length ? (
        <Section title="Reviews">
          <List>
            {reviews.map((r) => (
              <ListRow key={r.id} title={GRADES[r.grade].label} value={r.day} testID={`review-${r.id}`} />
            ))}
          </List>
        </Section>
      ) : null}
    </Screen>
  );
}
