import { useQuery } from '@homeai/sdk';
import { BarChart, Card, List, ListRow, Screen, Section, formatMoney } from '@homeai/ui';
import { CURRENCY, addMonths, monthLabel, thisMonth } from '../../lib/budget';

const MONTHS = 6;

type Month = { month: string; spent: number; income: number };
type Average = { name: string; average: number };

export default function TrendsScreen() {
  const last = thisMonth();
  const first = addMonths(last, -(MONTHS - 1));
  const { data } = useQuery<Month>(
    `SELECT substr(day, 1, 7) AS month,
            coalesce(-sum(CASE WHEN amount < 0 THEN amount END), 0) AS spent,
            coalesce(sum(CASE WHEN amount > 0 THEN amount END), 0) AS income
     FROM transactions WHERE day >= ? GROUP BY month`,
    [`${first}-01`],
  );
  const { data: averages } = useQuery<Average>(
    `SELECT coalesce(c.name, 'Uncategorized') AS name, -sum(t.amount) / ? AS average
     FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
     WHERE t.amount < 0 AND t.day >= ? GROUP BY t.category_id ORDER BY average DESC`,
    [MONTHS, `${first}-01`],
  );
  // Every month in the window, including ones with nothing in them.
  const months = Array.from({ length: MONTHS }, (_, i) => addMonths(first, i)).map((m) => {
    const found = (data ?? []).find((d) => d.month === m);
    return { label: monthLabel(m, true), spent: found?.spent ?? 0, income: found?.income ?? 0 };
  });

  return (
    <Screen>
      <Section title={`The last ${MONTHS} months`}>
        <Card>
          <BarChart
            data={months}
            x="label"
            y={['spent', 'income']}
            names={{ spent: 'Spent', income: 'Income' }}
            title="Spent and income per month"
            formatY={(n) => formatMoney(n, CURRENCY)}
            testID="trend-chart"
          />
        </Card>
      </Section>
      {averages?.length ? (
        <Section title="Average a month">
          <List>
            {averages.map((a) => (
              <ListRow key={a.name} title={a.name} value={formatMoney(a.average, CURRENCY)} testID={`average-${a.name}`} />
            ))}
          </List>
        </Section>
      ) : null}
    </Screen>
  );
}
