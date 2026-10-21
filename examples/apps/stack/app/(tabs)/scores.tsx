import { Text, View } from 'react-native';
import { useMembers, useQuery, useUser } from '@homeai/sdk';
import { Card, EmptyState, List, ListRow, Screen, Section, useTheme } from '@homeai/ui';

type Leader = { player: string; best: number; games: number };
type Game = { id: number; score: number; perfects: number; _created_at: string };

function when(iso: string) {
  return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

/** Your best and recent games, then each member's best. */
export default function ScoresScreen() {
  const t = useTheme();
  const me = useUser();
  const members = useMembers();
  const mine = useQuery<Game>('SELECT id, score, perfects, _created_at FROM games WHERE _created_by = ? ORDER BY _created_at DESC, id DESC LIMIT 10', [me?.id ?? '']).data ?? [];
  const leaders = useQuery<Leader>('SELECT _created_by AS player, MAX(score) AS best, COUNT(*) AS games FROM games GROUP BY _created_by ORDER BY best DESC, games ASC').data ?? [];
  const myLine = leaders.find((l) => l.player === me?.id);
  const name = (id: string) => members.find((m) => m.id === id)?.name ?? 'Someone';
  const number = (label: string, value: number, testID: string) => (
    <View style={{ flex: 1, alignItems: 'center' }}>
      <Text style={{ fontSize: t.font.large, fontWeight: '700', color: t.colors.text }} testID={testID}>
        {value}
      </Text>
      <Text style={{ fontSize: t.font.small, color: t.colors.textMuted }}>{label}</Text>
    </View>
  );

  return (
    <Screen>
      <Card>
        <View style={{ flexDirection: 'row' }}>
          {number('Your best', myLine?.best ?? 0, 'stat-best')}
          {number('Games', myLine?.games ?? 0, 'stat-games')}
        </View>
      </Card>
      <Section title="Leaderboard">
        {leaders.length ? (
          <List>
            {leaders.map((l, i) => {
              const m = members.find((x) => x.id === l.player);
              return (
                <ListRow
                  key={l.player}
                  title={`${i + 1}. ${name(l.player)}${l.player === me?.id ? ' (you)' : ''}`}
                  subtitle={`${l.games} ${l.games === 1 ? 'game' : 'games'}`}
                  value={String(l.best)}
                  icon={i === 0 ? 'trophy-outline' : 'person-outline'}
                  testID={`leader-${m?.username ?? l.player}`}
                />
              );
            })}
          </List>
        ) : (
          <EmptyState icon="podium-outline" title="No games yet" message="Finish a game on the Play tab and it shows up here." />
        )}
      </Section>
      {mine.length ? (
        <Section title="Your recent games">
          <List>
            {mine.map((g, i) => (
              <ListRow key={g.id} title={`${g.score} ${g.score === 1 ? 'block' : 'blocks'}`} subtitle={g.perfects ? `${g.perfects} perfect` : undefined} value={when(g._created_at)} icon="cube-outline" testID={`recent-${i}`} />
            ))}
          </List>
        </Section>
      ) : null}
    </Screen>
  );
}
