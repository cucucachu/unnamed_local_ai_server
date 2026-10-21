import { Text, View } from 'react-native';
import { useMembers, useQuery, useUser } from '@homeai/sdk';
import { Card, List, ListRow, Screen, Section, useTheme } from '@homeai/ui';
import { statsOf, type Game } from '../../lib/game';

/** Your stats (played, win %, streaks, how many guesses your wins took), then everyone's in a list. */
export default function StatsScreen() {
  const t = useTheme();
  const me = useUser();
  const members = useMembers();
  const { data } = useQuery<Pick<Game, 'day' | 'guesses' | 'status' | '_created_by'>>("SELECT day, guesses, status, _created_by FROM games WHERE status <> 'playing'");
  const games = data ?? [];
  const statsFor = (id: string) => statsOf(games.filter((g) => g._created_by === id));
  const mine = statsFor(me?.id ?? '');
  const pct = (s: { played: number; won: number }) => (s.played ? Math.round((100 * s.won) / s.played) : 0);
  const most = Math.max(1, ...mine.spread);
  const number = (label: string, value: number, testID: string) => (
    <View style={{ flex: 1, alignItems: 'center' }}>
      <Text style={{ fontSize: t.font.large, fontWeight: '700', color: t.colors.text }} testID={testID}>
        {value}
      </Text>
      <Text style={{ fontSize: t.font.small, color: t.colors.textMuted, textAlign: 'center' }}>{label}</Text>
    </View>
  );

  return (
    <Screen>
      <Card>
        <View style={{ flexDirection: 'row' }}>
          {number('Played', mine.played, 'stat-played')}
          {number('Win %', pct(mine), 'stat-win')}
          {number('Streak', mine.current, 'stat-streak')}
          {number('Best streak', mine.best, 'stat-best')}
        </View>
      </Card>
      <Section title="Your wins took">
        <View style={{ gap: 4 }}>
          {mine.spread.map((n, i) => (
            <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <Text style={{ width: 14, color: t.colors.text }}>{i + 1}</Text>
              <View style={{ flex: 1 }}>
                <View style={{ width: `${Math.max(8, (100 * n) / most)}%`, backgroundColor: n ? t.colors.success : t.colors.inactive, borderRadius: 3, paddingHorizontal: 6 }}>
                  <Text style={{ color: '#ffffff', fontWeight: '700', textAlign: 'right' }} testID={`spread-${i + 1}`}>
                    {n}
                  </Text>
                </View>
              </View>
            </View>
          ))}
        </View>
      </Section>
      <Section title="Everyone">
        <List>
          {members
            .filter((m) => m.role !== 'viewer')
            .map((m) => ({ m, s: statsFor(m.id) }))
            .sort((a, b) => b.s.current - a.s.current || pct(b.s) - pct(a.s))
            .map(({ m, s }) => (
              <ListRow
                key={m.id}
                title={m.name}
                subtitle={s.played ? `${s.played} played · ${pct(s)}% won · streak ${s.current} (best ${s.best})` : 'Not played yet'}
                icon="person-outline"
                testID={`stats-${m.username}`}
              />
            ))}
        </List>
      </Section>
    </Screen>
  );
}
