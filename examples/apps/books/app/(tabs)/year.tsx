import { useQuery } from '@homeai/sdk';
import { BarChart, Card, EmptyState, List, ListRow, Screen, Section } from '@homeai/ui';
import { KINDS, kindOf, stars, type Kind } from '../../lib/books';

type Done = { id: number; title: string; creator: string; kind: Kind; rating: number | null; finished_on: string };

export default function YearScreen() {
  const year = new Date().getFullYear();
  const { data } = useQuery<Done>(
    "SELECT id, title, creator, kind, rating, finished_on FROM items WHERE status = 'finished' AND finished_on >= ? AND finished_on < ? ORDER BY finished_on",
    [`${year}-01-01`, `${year + 1}-01-01`],
  );
  const done = data ?? [];
  if (!done.length) {
    return (
      <Screen>
        <EmptyState icon="trophy-outline" title={`Nothing finished in ${year} yet`} message="What you finish shows up here, month by month." testID="year-empty" />
      </Screen>
    );
  }
  const months = Array.from({ length: 12 }, (_, m) => {
    const key = `${year}-${String(m + 1).padStart(2, '0')}`;
    const row: Record<string, string | number> = { label: new Date(year, m, 1).toLocaleDateString(undefined, { month: 'short' }) };
    for (const k of KINDS) row[k.value] = done.filter((d) => d.kind === k.value && d.finished_on.startsWith(key)).length;
    return row;
  });
  const kinds = KINDS.filter((k) => done.some((d) => d.kind === k.value));
  const rated = done.filter((d) => d.rating !== null);
  const average = rated.length ? rated.reduce((n, d) => n + d.rating!, 0) / rated.length : null;
  const best = [...rated].sort((a, b) => b.rating! - a.rating! || b.finished_on.localeCompare(a.finished_on)).slice(0, 5);

  return (
    <Screen>
      <List>
        {kinds.map((k) => (
          <ListRow key={k.value} title={`${k.label}s finished`} icon={k.icon} value={done.filter((d) => d.kind === k.value).length} testID={`year-${k.value}`} />
        ))}
        {average !== null ? <ListRow title="Average rating" value={`${average.toFixed(1)} ★`} testID="year-average" /> : null}
      </List>
      <Section title={`${year}, month by month`}>
        <Card>
          <BarChart
            data={months}
            x="label"
            y={kinds.map((k) => k.value)}
            names={Object.fromEntries(kinds.map((k) => [k.value, `${k.label}s`]))}
            title={`Finished per month in ${year}`}
            testID="year-chart"
          />
        </Card>
      </Section>
      {best.length ? (
        <Section title="Your favorites">
          <List>
            {best.map((d) => (
              <ListRow key={d.id} title={d.title} subtitle={d.creator || kindOf(d.kind).label} value={stars(d.rating)} testID={`best-${d.id}`} />
            ))}
          </List>
        </Section>
      ) : null}
    </Screen>
  );
}
