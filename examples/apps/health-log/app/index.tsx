import { useRouter } from 'expo-router';
import { runAction, useQuery, useSpace } from '@homeai/sdk';
import { Button, EmptyState, List, ListRow, Screen } from '@homeai/ui';
import { DAILY, format, whenLabel, type Measure } from '../lib/health';

type Row = Measure & { day: string | null; value: number | null };

export default function MeasuresScreen() {
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  // Each measure with its latest day's value.
  const { data } = useQuery<Row>(
    `WITH daily AS (${DAILY})
     SELECT m.id, m.name, m.unit, m.goal, m.decimals, m.summary, d.day, d.value
     FROM measures m
     LEFT JOIN daily d ON d.measure_id = m.id AND d.day = (SELECT max(day) FROM entries WHERE measure_id = m.id)
     ORDER BY m.name COLLATE NOCASE`,
  );
  const measures = data ?? [];

  return (
    <Screen>
      <List
        data={measures}
        keyExtractor={(m) => m.id}
        empty={
          <EmptyState
            icon="pulse-outline"
            title="Nothing measured yet"
            message="Track anything over time: weight, sleep, water, steps…"
            action={canEdit ? { title: 'Add weight, sleep and water', onPress: () => void runAction('addStarterMeasures', {}) } : undefined}
            testID="measures-empty"
          />
        }
        renderItem={(m) => (
          <ListRow
            title={m.name}
            subtitle={
              [m.day && m.value !== null ? `${whenLabel(m.day)}` : 'No readings yet', m.goal !== null ? `goal ${format(m.goal, m)}` : null].filter(Boolean).join(' · ')
            }
            value={m.value !== null ? format(m.value, m) : undefined}
            onPress={() => router.push({ pathname: '/measure/[id]', params: { id: String(m.id) } })}
            testID={`measure-${m.id}`}
          />
        )}
        testID="measures"
      />
      {canEdit ? <Button title="New measure" variant="secondary" icon="add-circle-outline" onPress={() => router.push('/new-measure')} testID="new-measure" /> : null}
    </Screen>
  );
}
