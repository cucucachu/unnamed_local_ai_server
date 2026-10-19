import { useState } from 'react';
import { Text, View } from 'react-native';
import { Button, DateField, TextField, Toggle, useTheme } from '@homeai/ui';
import type { Countdown } from '../lib/countdowns';

export type CountdownValues = Omit<Countdown, 'id'>;

export function CountdownForm({ initial, saveTitle, onSave }: { initial: CountdownValues; saveTitle: string; onSave: (v: CountdownValues) => Promise<void> }) {
  const t = useTheme();
  const [title, setTitle] = useState(initial.title);
  const [day, setDay] = useState<string | null>(initial.day || null);
  const [yearly, setYearly] = useState(initial.yearly === 1);
  const [note, setNote] = useState(initial.note);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (!title.trim()) return setError('What is it?');
    if (!day) return setError('Pick the date.');
    setError(null);
    try {
      await onSave({ title: title.trim(), day, yearly: yearly ? 1 : 0, note: note.trim() });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <View style={{ gap: t.spacing.md }}>
      <TextField label="What" value={title} onChangeText={setTitle} placeholder="e.g. Lisbon trip" testID="cd-title" />
      <DateField label={yearly ? 'When it first happened' : 'Date'} value={day} onChange={setDay} testID="cd-day" />
      <Toggle label="Every year" description="An anniversary: counts to the next one" value={yearly} onValueChange={setYearly} testID="cd-yearly" />
      <TextField label="Note" value={note} onChangeText={setNote} multiline testID="cd-note" />
      {error ? (
        <Text role="alert" style={{ color: t.colors.danger }} testID="cd-error">
          {error}
        </Text>
      ) : null}
      <Button title={saveTitle} onPress={save} testID="cd-save" />
    </View>
  );
}
