import { useState } from 'react';
import { Text, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { runAction, useQuery, useSpace } from '@homeai/sdk';
import { Button, Card, EmptyState, Screen, useTheme } from '@homeai/ui';
import { CARD_COLUMNS, GRADES, intervalLabel, nextInterval, today, type Card as CardRow } from '../../lib/flashcards';

/**
 * A review session: the deck's due cards one at a time (every deck's for
 * `all`). Cards graded "Again" are due today, so they come back after the
 * ones not seen yet today.
 */
export default function StudyScreen() {
  const t = useTheme();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const { deck } = useLocalSearchParams<{ deck: string }>();
  const deckId = deck === 'all' ? 0 : Number(deck);
  const now = today();
  const { data: queue } = useQuery<CardRow>(
    `SELECT ${CARD_COLUMNS} FROM cards c
     WHERE c.due <= ? AND (? = 0 OR c.deck_id = ?)
     ORDER BY (SELECT max(r.id) FROM reviews r WHERE r.card_id = c.id AND r.day = ?), c.due, c.id`,
    [now, deckId, deckId, now],
  );
  const { data: done } = useQuery<{ n: number }>(
    'SELECT count(*) AS n FROM reviews r JOIN cards c ON c.id = r.card_id WHERE r.day = ? AND (? = 0 OR c.deck_id = ?)',
    [now, deckId, deckId],
  );
  const [shown, setShown] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const card = queue?.[0];
  const reviewed = done?.[0]?.n ?? 0;

  async function grade(g: number) {
    setBusy(true);
    try {
      await runAction('grade', { card_id: card!.id, grade: g, today: today() });
      setShown(null);
    } finally {
      setBusy(false);
    }
  }

  if (!queue) return <Screen />;
  if (!card) {
    return (
      <Screen>
        <Stack.Screen options={{ title: 'Study' }} />
        <EmptyState
          icon="checkmark-done-outline"
          title="All done for today"
          message={reviewed ? `${reviewed} review${reviewed === 1 ? '' : 's'} today. The next cards come due on their own.` : 'Nothing is due.'}
          action={{ title: 'Close', onPress: () => router.back() }}
          testID="study-done"
        />
      </Screen>
    );
  }
  const revealed = shown === card.id;
  return (
    <Screen>
      <Stack.Screen options={{ title: `${queue.length} to go` }} />
      <Card>
        <View style={{ gap: t.spacing.md, paddingVertical: t.spacing.lg }}>
          <Text style={{ color: t.colors.text, fontSize: t.font.large, textAlign: 'center' }} testID="study-front">
            {card.front}
          </Text>
          {revealed ? (
            <>
              <View style={{ height: 1, backgroundColor: t.colors.border }} />
              <Text style={{ color: t.colors.text, fontSize: t.font.title, textAlign: 'center' }} testID="study-back">
                {card.back || '(no answer written)'}
              </Text>
            </>
          ) : null}
        </View>
      </Card>
      {!revealed ? (
        <Button title="Show answer" onPress={() => setShown(card.id)} testID="study-show" />
      ) : canEdit ? (
        <View style={{ flexDirection: 'row', gap: t.spacing.sm }}>
          {GRADES.map((g) => (
            <View key={g.grade} style={{ flex: 1 }}>
              <Button
                title={`${g.label}\n${intervalLabel(nextInterval(card, g.grade))}`}
                variant={g.grade === 0 ? 'destructive' : g.grade === 2 ? 'primary' : 'secondary'}
                disabled={busy}
                onPress={() => void grade(g.grade)}
                testID={`grade-${g.grade}`}
              />
            </View>
          ))}
        </View>
      ) : (
        <Text style={{ color: t.colors.textMuted }}>Viewers can look but not grade.</Text>
      )}
    </Screen>
  );
}
