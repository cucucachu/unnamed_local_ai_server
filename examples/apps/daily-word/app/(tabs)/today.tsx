import { View } from 'react-native';
import { useMembers, useQuery, useSpace, useUser } from '@homeai/sdk';
import { List, ListRow, Screen, Section, useTheme } from '@homeai/ui';
import { markColor } from '../../components/Board';
import { GAME_COLUMNS, guessesOf, mark, today, type Game } from '../../lib/game';

function summary(g: Game | undefined): string {
  if (!g) return 'Not played yet';
  const n = guessesOf(g).length;
  if (g.status === 'won') return `Solved in ${n}`;
  if (g.status === 'lost') return 'Out of guesses';
  return `Playing (${n} guess${n === 1 ? '' : 'es'} so far)`;
}

/** Everyone's game today. Their colored grids (no letters) show once you've finished yours. */
export default function TodayScreen() {
  const t = useTheme();
  const me = useUser();
  const members = useMembers();
  const viewer = useSpace()?.role === 'viewer';
  const day = today();
  const { data } = useQuery<Game>(`SELECT ${GAME_COLUMNS} FROM games WHERE day = ? ORDER BY id`, [day]);
  const games = data ?? [];
  const gameOf = (id: string) => games.find((g) => g._created_by === id);
  const mine = me ? gameOf(me.id) : undefined;
  const reveal = viewer || mine?.status === 'won' || mine?.status === 'lost';
  const players = members.filter((m) => m.role !== 'viewer');
  // Solved first, fewest guesses first; then playing, then not yet.
  const order = (id: string) => {
    const g = gameOf(id);
    if (!g) return 100;
    if (g.status === 'won') return guessesOf(g).length;
    return g.status === 'lost' ? 10 : 20;
  };
  const sorted = [...players].sort((a, b) => order(a.id) - order(b.id) || a.name.localeCompare(b.name));

  return (
    <Screen>
      <Section title={`Today, ${day}`} footer={reveal ? undefined : 'Finish yours to see everyone’s grids.'}>
        <List>
          {sorted.map((m) => {
            const g = gameOf(m.id);
            return (
              <ListRow key={m.id} title={m.id === me?.id ? `${m.name} (you)` : m.name} subtitle={summary(g)} icon={g?.status === 'won' ? 'trophy-outline' : 'person-outline'} testID={`player-${m.username}`} />
            );
          })}
        </List>
      </Section>
      {reveal
        ? sorted
            .map((m) => ({ m, g: gameOf(m.id) }))
            .filter(({ g }) => g && g.status !== 'playing')
            .map(({ m, g }) => (
              <Section key={m.id} title={m.name}>
                <View style={{ gap: 3 }} testID={`grid-${m.username}`}>
                  {guessesOf(g).map((guess, r) => (
                    <View key={r} style={{ flexDirection: 'row', gap: 3 }}>
                      {mark(guess, g!.word).map((k, c) => (
                        <View key={c} style={{ width: 18, height: 18, borderRadius: 3, backgroundColor: markColor(t, k) }} />
                      ))}
                    </View>
                  ))}
                </View>
              </Section>
            ))
        : null}
    </Screen>
  );
}
