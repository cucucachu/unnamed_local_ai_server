import { useState } from 'react';
import { Text, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { BarChart, Button, Card, Chip, LineChart, List, ListRow, Screen, Section, useTheme } from '@homeai/ui';
import { MeasureForm, type MeasureValues } from '../../components/MeasureForm';
import { DAILY, MEASURE_COLUMNS, daysAgo, format, whenLabel, type Measure } from '../../lib/health';

const RANGES = [
  { label: '30 days', days: 30 },
  { label: '90 days', days: 90 },
  { label: 'Year', days: 365 },
  { label: 'All', days: null },
] as const;

export default function MeasureScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Measure>(`SELECT ${MEASURE_COLUMNS} FROM measures WHERE id = ?`, [Number(id)]);
  const measure = data?.[0];
  if (!measure) {
    return (
      <Screen>
        <Text testID="measure-missing">{loading ? 'Loading…' : 'This measure was deleted.'}</Text>
      </Screen>
    );
  }
  return <MeasureDetail key={measure.id} measure={measure} />;
}

function MeasureDetail({ measure }: { measure: Measure }) {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const [range, setRange] = useState<(typeof RANGES)[number]['label']>('30 days');
  const days = RANGES.find((r) => r.label === range)?.days ?? null;
  const since = days === null ? '0000-00-00' : daysAgo(days - 1);
  const { data: daily } = useQuery<{ day: string; value: number }>(
    `WITH daily AS (${DAILY}) SELECT day, value FROM daily WHERE measure_id = ? AND day >= ? ORDER BY day`,
    [measure.id, since],
  );
  const { data: entries } = useQuery<{ id: number; day: string; value: number; note: string }>(
    'SELECT id, day, value, note FROM entries WHERE measure_id = ? ORDER BY day DESC, id DESC LIMIT 100',
    [measure.id],
  );
  const points = (daily ?? []).map((d) => ({ label: d.day.slice(5), value: Number(d.value.toFixed(measure.decimals)), goal: measure.goal }));
  const values = points.map((p) => p.value);
  const average = values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
  const met = measure.goal !== null ? values.filter((v) => v >= measure.goal!).length : null;
  const Chart = measure.summary === 'sum' ? BarChart : LineChart;

  async function save(v: MeasureValues) {
    await db.runAsync('UPDATE measures SET name = ?, unit = ?, goal = ?, decimals = ?, summary = ? WHERE id = ?', [v.name, v.unit, v.goal, v.decimals, v.summary, measure.id]);
  }

  async function remove() {
    await db.runAsync('DELETE FROM measures WHERE id = ?', [measure.id]);
    router.back();
  }

  return (
    <Screen>
      <Stack.Screen options={{ title: measure.name }} />
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: t.spacing.sm }}>
        {RANGES.map((r) => (
          <Chip key={r.label} label={r.label} selected={range === r.label} onPress={() => setRange(r.label)} testID={`range-${r.days ?? 'all'}`} />
        ))}
      </View>
      <Card>
        <Chart
          data={points}
          x="label"
          y={measure.goal !== null && measure.summary !== 'sum' ? ['value', 'goal'] : 'value'}
          names={{ value: measure.name, goal: 'Goal' }}
          title={`${measure.name}${measure.unit ? ` (${measure.unit})` : ''}`}
          formatY={(n) => format(n, measure)}
          testID="measure-chart"
        />
      </Card>
      <Text style={{ color: t.colors.textMuted }} testID="measure-stats">
        {average === null
          ? 'No readings in this range.'
          : [`Average ${format(average, measure)} over ${values.length} day${values.length === 1 ? '' : 's'}`, met !== null ? `goal met on ${met}` : null]
              .filter(Boolean)
              .join(' · ')}
      </Text>
      {canEdit ? (
        <Button title={`Log ${measure.name.toLowerCase()}`} icon="add-outline" onPress={() => router.push({ pathname: '/log', params: { measure: String(measure.id) } })} testID="measure-log" />
      ) : null}
      <Section title="Readings" footer={canEdit ? 'Long-press one to delete it.' : undefined}>
        <List
          data={entries ?? []}
          keyExtractor={(e) => e.id}
          empty={<Text style={{ color: t.colors.textMuted, padding: t.spacing.md }}>None yet.</Text>}
          renderItem={(e) => (
            <ListRow
              title={format(e.value, measure)}
              subtitle={[whenLabel(e.day), e.note].filter(Boolean).join(' · ')}
              actions={canEdit ? [{ label: 'Delete', icon: 'trash-outline', onPress: () => void db.runAsync('DELETE FROM entries WHERE id = ?', [e.id]) }] : undefined}
              testID={`entry-${e.id}`}
            />
          )}
        />
      </Section>
      {canEdit ? (
        <>
          <Section title="Settings">
            <MeasureForm initial={measure} saveTitle="Save" onSave={save} />
          </Section>
          <Button title="Delete measure and its readings" variant="destructive" onPress={remove} testID="measure-delete" />
        </>
      ) : null}
    </Screen>
  );
}
