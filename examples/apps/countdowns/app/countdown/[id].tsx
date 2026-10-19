import { Text } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { Button, Card, Screen, useTheme } from '@homeai/ui';
import { CountdownForm, type CountdownValues } from '../../components/CountdownForm';
import { COUNTDOWN_COLUMNS, daysLabel, daysUntil, nextYearly, ordinal, type Countdown } from '../../lib/countdowns';

export default function CountdownScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading } = useQuery<Countdown>(`SELECT ${COUNTDOWN_COLUMNS} FROM countdowns WHERE id = ?`, [Number(id)]);
  const c = data?.[0];

  if (!c) {
    return (
      <Screen>
        <Text testID="cd-missing">{loading ? 'Loading…' : 'This countdown was deleted.'}</Text>
      </Screen>
    );
  }
  const next = c.yearly ? nextYearly(c.day) : { days: daysUntil(c.day), years: 0 };

  async function save(v: CountdownValues) {
    await db.runAsync('UPDATE countdowns SET title = ?, day = ?, yearly = ?, note = ? WHERE id = ?', [v.title, v.day, v.yearly, v.note, c!.id]);
    router.back();
  }

  async function remove() {
    await db.runAsync('DELETE FROM countdowns WHERE id = ?', [c!.id]);
    router.back();
  }

  return (
    <Screen>
      <Stack.Screen options={{ title: c.title }} />
      <Card>
        <Text style={{ color: t.colors.text, fontSize: t.font.large, fontWeight: '600' }} testID="cd-when">
          {[daysLabel(next.days), c.yearly && next.years > 0 ? `${ordinal(next.years)} anniversary` : null].filter(Boolean).join(' · ')}
        </Text>
      </Card>
      {canEdit ? (
        <>
          <CountdownForm key={`${c.id}:${c.day}:${c.yearly}`} initial={c} saveTitle="Save" onSave={save} />
          <Button title="Delete countdown" variant="destructive" onPress={remove} testID="cd-delete" />
        </>
      ) : c.note ? (
        <Text style={{ color: t.colors.text }}>{c.note}</Text>
      ) : null}
    </Screen>
  );
}
