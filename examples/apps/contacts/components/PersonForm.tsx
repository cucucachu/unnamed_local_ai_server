import { useState } from 'react';
import { Text, View } from 'react-native';
import { Button, DateField, TextField, useTheme } from '@homeai/ui';
import type { Person } from '../lib/contacts';

export type PersonValues = Omit<Person, 'id'>;

export function PersonForm({ initial, saveTitle, onSave }: { initial: PersonValues; saveTitle: string; onSave: (v: PersonValues) => Promise<void> }) {
  const t = useTheme();
  const [name, setName] = useState(initial.name);
  const [birthday, setBirthday] = useState<string | null>(initial.birthday);
  const [notes, setNotes] = useState(initial.notes);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!name.trim()) return setError('Give them a name.');
    setError(null);
    setSaving(true);
    try {
      await onSave({ name: name.trim(), birthday, notes: notes.trim() });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <View style={{ gap: t.spacing.md }}>
      <TextField label="Name" value={name} onChangeText={setName} placeholder="e.g. Priya Shah" testID="person-name" />
      <DateField label="Birthday" value={birthday} onChange={setBirthday} hint="Leave empty if you don't know it" testID="person-birthday" />
      <TextField label="Notes" value={notes} onChangeText={setNotes} multiline testID="person-notes" />
      {error ? (
        <Text role="alert" style={{ color: t.colors.danger }} testID="person-error">
          {error}
        </Text>
      ) : null}
      <Button title={saveTitle} onPress={save} loading={saving} testID="person-save" />
    </View>
  );
}
