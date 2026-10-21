import { useState } from 'react';
import { Text, View } from 'react-native';
import { useDatabase, useQuery, useSpace, useUser } from '@homeai/sdk';
import { useTheme } from '@homeai/ui';
import { StackGame } from '../../components/StackGame';

/** The game, full screen. Each finished game is saved as a row; your best comes from those. */
export default function PlayScreen() {
  const t = useTheme();
  const db = useDatabase();
  const me = useUser();
  const canPlay = useSpace()?.role !== 'viewer';
  const [problem, setProblem] = useState<string | null>(null);
  const { data } = useQuery<{ best: number | null }>('SELECT MAX(score) AS best FROM games WHERE _created_by = ?', [me?.id ?? '']);
  const best = data?.[0]?.best ?? 0;

  async function save(score: number, perfects: number) {
    try {
      await db.runAsync('INSERT INTO games (score, perfects) VALUES (?, ?)', [score, perfects]);
      setProblem(null);
    } catch (e) {
      setProblem(`Couldn't save that game: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  return (
    <View style={{ flex: 1, backgroundColor: t.colors.background }}>
      <StackGame canPlay={canPlay} best={best} onOver={save} />
      {problem ? (
        <Text testID="problem" style={{ position: 'absolute', bottom: t.spacing.md, left: t.spacing.md, right: t.spacing.md, color: t.colors.danger, textAlign: 'center' }}>
          {problem}
        </Text>
      ) : null}
    </View>
  );
}
