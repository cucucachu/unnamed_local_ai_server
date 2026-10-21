import { useEffect, useRef, useState } from 'react';
import { Text, TextInput, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useDatabase, useQuery, useSpace, useUser } from '@homeai/sdk';
import { Button, Card, Screen, useTheme } from '@homeai/ui';
import { Board, Keyboard } from '../../components/Board';
import { GAME_COLUMNS, LENGTH, MAX_GUESSES, guessesOf, isWord, keyMarks, today, wordFor, type Game } from '../../lib/game';

export default function PlayScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const me = useUser();
  const canPlay = useSpace()?.role !== 'viewer';
  const day = today();
  const { data, loading } = useQuery<Game>(`SELECT ${GAME_COLUMNS} FROM games WHERE day = ? AND _created_by = ? ORDER BY id LIMIT 1`, [day, me?.id ?? '']);
  const game = data?.[0] ?? null;
  // A game already saved keeps the word it started with.
  const word = game?.word ?? wordFor(day);
  const guesses = guessesOf(game);
  const over = game?.status === 'won' || game?.status === 'lost';
  const [typing, setTyping] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const keys = useRef<TextInput>(null);

  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(() => setMessage(null), 1800);
    return () => clearTimeout(timer);
  }, [message]);

  async function submit() {
    if (typing.length < LENGTH) return setMessage('Not enough letters');
    if (!isWord(typing)) return setMessage('Not in the word list');
    const next = [...guesses, typing];
    const status = typing === word ? 'won' : next.length >= MAX_GUESSES ? 'lost' : 'playing';
    setSaving(true);
    try {
      if (game) await db.runAsync('UPDATE games SET guesses = ?, status = ? WHERE id = ?', [next.join(','), status, game.id]);
      else await db.runAsync('INSERT INTO games (day, word, guesses, status) VALUES (?, ?, ?, ?)', [day, word, next.join(','), status]);
      setTyping('');
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  function press(key: string) {
    if (over || saving || !canPlay) return;
    if (key === 'enter') void submit();
    else if (key === 'back') setTyping((s) => s.slice(0, -1));
    else if (/^[a-z]$/.test(key)) setTyping((s) => (s.length < LENGTH ? s + key : s));
    keys.current?.focus();
  }

  if (loading && !data) return null;
  return (
    <Screen>
      {/* Takes a hardware keyboard's keys without opening the phone's (inputMode none). */}
      {canPlay && !over ? (
        <TextInput
          ref={keys}
          autoFocus
          inputMode="none"
          value=""
          onKeyPress={(e) => {
            const k = e.nativeEvent.key;
            press(k === 'Enter' ? 'enter' : k === 'Backspace' ? 'back' : k.toLowerCase());
          }}
          style={{ position: 'absolute', width: 1, height: 1, opacity: 0 }}
          testID="hardware-keys"
        />
      ) : null}
      <View style={{ minHeight: 24, alignItems: 'center' }}>
        {message ? (
          <Text style={{ color: t.colors.text, fontWeight: '600' }} testID="message">
            {message}
          </Text>
        ) : null}
      </View>
      <Board guesses={guesses} word={word} typing={over ? '' : typing} />
      {over ? (
        <Card testID="result">
          <Text style={{ fontSize: t.font.title, fontWeight: '700', color: t.colors.text, textAlign: 'center' }} testID="result-title">
            {game!.status === 'won' ? `Got it in ${guesses.length}!` : `The word was ${word.toUpperCase()}`}
          </Text>
          <Text style={{ color: t.colors.textMuted, textAlign: 'center' }}>A new word tomorrow.</Text>
          <Button title="See how everyone did" variant="secondary" icon="people-outline" onPress={() => router.push('/today')} testID="see-today" />
        </Card>
      ) : canPlay ? (
        <Keyboard marks={keyMarks(guesses, word)} onKey={press} disabled={saving} />
      ) : (
        <Text style={{ color: t.colors.textMuted, textAlign: 'center' }} testID="view-only">
          View only: you can see how everyone did, but not play.
        </Text>
      )}
    </Screen>
  );
}
