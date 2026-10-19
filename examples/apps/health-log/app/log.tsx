import { useState } from 'react';
import { Text } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useDatabase, useQuery } from '@homeai/sdk';
import { Button, DateField, NumberField, Screen, Select, TextField, useTheme } from '@homeai/ui';
import { DAILY, MEASURE_COLUMNS, format, today, type Measure } from '../lib/health';

/** The modal for one reading; `?measure=<id>` picks the measure. */
export default function LogScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const params = useLocalSearchParams<{ measure?: string }>();
  const { data: measures } = useQuery<Measure>(`SELECT ${MEASURE_COLUMNS} FROM measures ORDER BY name COLLATE NOCASE`);
  const [picked, setPicked] = useState<number | null>(params.measure ? Number(params.measure) : null);
  const [value, setValue] = useState<number | null>(null);
  const [day, setDay] = useState<string | null>(today());
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const measure = (measures ?? []).find((m) => m.id === picked);
  const { data: sofar } = useQuery<{ value: number }>(`WITH daily AS (${DAILY}) SELECT value FROM daily WHERE measure_id = ? AND day = ?`, [picked ?? 0, day ?? '']);

  async function save() {
    if (!measure) return setError('Pick what you measured.');
    if (value === null) return setError('Enter the reading.');
    if (!day) return setError('Pick the day.');
    await db.runAsync('INSERT INTO entries (measure_id, day, value, note) VALUES (?, ?, ?, ?)', [measure.id, day, value, note.trim()]);
    router.back();
  }

  return (
    <Screen>
      <Select
        label="Measure"
        value={picked}
        options={(measures ?? []).map((m) => ({ label: m.name, value: m.id }))}
        onChange={setPicked}
        placeholder={measures?.length ? 'Pick one' : 'Add a measure first'}
        testID="log-measure"
      />
      <NumberField label={measure?.unit ? `Value (${measure.unit})` : 'Value'} value={value} onChange={setValue} decimals={measure?.decimals ?? 2} testID="log-value" />
      <DateField label="Day" value={day} onChange={setDay} testID="log-day" />
      {measure?.summary === 'sum' && sofar?.[0] ? (
        <Text style={{ color: t.colors.textMuted }} testID="log-sofar">
          {format(sofar[0].value, measure)} so far that day; this adds to it.
        </Text>
      ) : null}
      <TextField label="Note" value={note} onChangeText={setNote} testID="log-note" />
      {error ? (
        <Text role="alert" style={{ color: t.colors.danger }} testID="log-error">
          {error}
        </Text>
      ) : null}
      <Button title="Log it" onPress={save} testID="log-save" />
    </Screen>
  );
}
