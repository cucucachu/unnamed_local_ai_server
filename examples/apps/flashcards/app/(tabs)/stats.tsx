import { useQuery } from '@homeai/sdk';
import { BarChart, Card, EmptyState, List, ListRow, Screen, Section } from '@homeai/ui';
import { LEARNED_DAYS, inDays, today } from '../../lib/flashcards';

const DAYS = 30;
const AHEAD = 14;

type Day = { day: string; remembered: number; forgot: number };
type Totals = { cards: number; due: number; learned: number; fresh: number };

const short = (day: string) => {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
};

export default function StatsScreen() {
  const now = today();
  const { data: days } = useQuery<Day>(
    `SELECT day, count(CASE WHEN grade > 0 THEN 1 END) AS remembered, count(CASE WHEN grade = 0 THEN 1 END) AS forgot
     FROM reviews WHERE day > ? GROUP BY day`,
    [inDays(-DAYS)],
  );
  const { data: totals } = useQuery<Totals>(
    `SELECT count(*) AS cards, count(CASE WHEN due <= ? THEN 1 END) AS due,
            count(CASE WHEN interval_days >= ? THEN 1 END) AS learned, count(CASE WHEN reps = 0 AND interval_days = 0 THEN 1 END) AS fresh
     FROM cards`,
    [now, LEARNED_DAYS],
  );
  const { data: ahead } = useQuery<{ day: string; n: number }>('SELECT due AS day, count(*) AS n FROM cards WHERE due > ? AND due <= ? GROUP BY due', [now, inDays(AHEAD)]);
  const tot = totals?.[0];

  // Every day in the window, including ones with no reviews.
  const history = Array.from({ length: DAYS }, (_, i) => inDays(i - DAYS + 1)).map((day) => {
    const found = days?.find((d) => d.day === day);
    return { label: short(day), remembered: found?.remembered ?? 0, forgot: found?.forgot ?? 0 };
  });
  const forecast = Array.from({ length: AHEAD }, (_, i) => inDays(i + 1)).map((day) => ({ label: short(day), cards: ahead?.find((a) => a.day === day)?.n ?? 0 }));
  const reviewed = history.reduce((n, d) => n + d.remembered + d.forgot, 0);
  const remembered = history.reduce((n, d) => n + d.remembered, 0);

  if (!tot?.cards) {
    return (
      <Screen>
        <EmptyState icon="stats-chart-outline" title="Nothing to show yet" message="Add cards and study them; your progress shows up here." testID="stats-empty" />
      </Screen>
    );
  }
  return (
    <Screen>
      <List>
        <ListRow title="Cards" value={tot.cards} testID="stat-cards" />
        <ListRow title="Due today" value={tot.due} testID="stat-due" />
        <ListRow title="New" subtitle="Not studied yet" value={tot.fresh} testID="stat-new" />
        <ListRow title="Learned" subtitle={`Spaced ${LEARNED_DAYS} days or more apart`} value={tot.learned} testID="stat-learned" />
        <ListRow
          title="Remembered"
          subtitle={`Of ${reviewed} review${reviewed === 1 ? '' : 's'} in the last ${DAYS} days`}
          value={reviewed ? `${Math.round((remembered / reviewed) * 100)}%` : '–'}
          testID="stat-remembered"
        />
      </List>
      <Section title={`The last ${DAYS} days`}>
        <Card>
          <BarChart data={history} x="label" y={['remembered', 'forgot']} names={{ remembered: 'Remembered', forgot: 'Forgot' }} title="Reviews per day" testID="reviews-chart" />
        </Card>
      </Section>
      <Section title={`The next ${AHEAD} days`}>
        <Card>
          <BarChart data={forecast} x="label" y="cards" names={{ cards: 'Cards due' }} title="Cards coming due" testID="forecast-chart" />
        </Card>
      </Section>
    </Screen>
  );
}
