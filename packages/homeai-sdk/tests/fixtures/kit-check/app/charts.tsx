import { BarChart, LineChart, PieChart, Screen, Section, formatMoney } from '@homeai/ui';

type Month = { month: string; spent: number; budget: number };

const MONTHS: Month[] = [
  { month: 'Jan', spent: 420, budget: 500 },
  { month: 'Feb', spent: 515, budget: 500 },
  { month: 'Mar', spent: 380, budget: 450 },
];
const NET = [
  { week: 'W1', net: 120 },
  { week: 'W2', net: -80 },
  { week: 'W3', net: 45 },
];
const WEIGHT = Array.from({ length: 60 }, (_, i) => ({ day: `2026-08-${String((i % 30) + 1).padStart(2, '0')}`, kg: 82 - i * 0.05 + (i % 3) * 0.2 }));
const GAP = [
  { day: 'Mon', km: 3 },
  { day: 'Tue', km: null },
  { day: 'Wed', km: 5 },
];
const CATEGORIES = [
  { name: 'Food', total: 300 },
  { name: 'Rent', total: 900 },
  { name: 'Fun', total: 100 },
  { name: 'Refund', total: -50 },
];

export default function Charts() {
  return (
    <Screen testID="charts-screen">
      <Section title="Bars">
        <BarChart testID="bar-two" title="Spending" data={MONTHS} x="month" y={['spent', 'budget']} names={{ spent: 'Spent', budget: 'Budget' }} formatY={(n) => formatMoney(n)} />
        <BarChart testID="bar-negative" data={NET} x="week" y="net" />
        <BarChart testID="bar-empty" data={[]} x="month" y="spent" />
      </Section>
      <Section title="Lines">
        <LineChart testID="line-many" title="Weight" data={WEIGHT} x="day" y="kg" formatX={(d) => String(d).slice(5)} />
        <LineChart testID="line-one" data={[{ day: 'Today', kg: 81.4 }]} x="day" y="kg" />
        <LineChart testID="line-gap" data={GAP} x="day" y="km" />
        <LineChart testID="line-empty" data={null} x="day" y="kg" />
      </Section>
      <Section title="Pie">
        <PieChart testID="pie" title="Categories" data={CATEGORIES} label="name" value="total" />
        <PieChart testID="pie-one" data={[{ name: 'All', total: 1 }]} label="name" value="total" />
        <PieChart testID="pie-empty" data={[]} label="name" value="total" />
      </Section>
    </Screen>
  );
}
