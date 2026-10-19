import { useState } from 'react';
import { Text, View } from 'react-native';
import { Button, NumberField, Select, Stepper, TextField, useTheme } from '@homeai/ui';
import { SUMMARIES, type Measure, type Summary } from '../lib/health';

export type MeasureValues = Omit<Measure, 'id'>;

export function MeasureForm({ initial, saveTitle, onSave }: { initial: MeasureValues; saveTitle: string; onSave: (v: MeasureValues) => Promise<void> }) {
  const t = useTheme();
  const [name, setName] = useState(initial.name);
  const [unit, setUnit] = useState(initial.unit);
  const [goal, setGoal] = useState<number | null>(initial.goal);
  const [decimals, setDecimals] = useState(initial.decimals);
  const [summary, setSummary] = useState<Summary>(initial.summary);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (!name.trim()) return setError('Give it a name.');
    setError(null);
    try {
      await onSave({ name: name.trim(), unit: unit.trim(), goal, decimals, summary });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <View style={{ gap: t.spacing.md }}>
      <TextField label="Name" value={name} onChangeText={setName} placeholder="e.g. Steps" testID="m-name" />
      <TextField label="Unit" value={unit} onChangeText={setUnit} placeholder="e.g. kg, hours, glasses" testID="m-unit" />
      <NumberField label="Daily goal" value={goal} onChange={setGoal} decimals={3} hint="Optional" testID="m-goal" />
      <Select label="A day's value is" value={summary} options={SUMMARIES} onChange={setSummary} testID="m-summary" />
      <Stepper label="Decimals" value={decimals} onChange={setDecimals} min={0} max={3} testID="m-decimals" />
      {error ? (
        <Text role="alert" style={{ color: t.colors.danger }} testID="m-error">
          {error}
        </Text>
      ) : null}
      <Button title={saveTitle} onPress={save} testID="m-save" />
    </View>
  );
}
