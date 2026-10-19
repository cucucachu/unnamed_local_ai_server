import { useState } from 'react';
import { Text, View } from 'react-native';
import { useMembers } from '@homeai/sdk';
import { Button, Chip, DateField, NumberField, Section, TextField, useTheme } from '@homeai/ui';

export type ChoreValues = { name: string; every_days: number; next_due: string; note: string; assignees: string[] };

/** The fields of a chore. `assignees` are user ids in rotation order (the order they were picked). */
export function ChoreForm({
  initial,
  saveTitle,
  onSave,
  editable = true,
}: {
  initial: ChoreValues;
  saveTitle: string;
  onSave: (values: ChoreValues) => Promise<void>;
  editable?: boolean;
}) {
  const t = useTheme();
  const members = useMembers();
  const [name, setName] = useState(initial.name);
  const [every, setEvery] = useState<number | null>(initial.every_days);
  const [nextDue, setNextDue] = useState<string | null>(initial.next_due);
  const [note, setNote] = useState(initial.note);
  const [assignees, setAssignees] = useState(initial.assignees);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const toggle = (id: string) =>
    setAssignees((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));

  async function save() {
    if (!name.trim()) return setError('Give the chore a name.');
    if (!every || every < 1) return setError('How often: at least every 1 day.');
    if (!nextDue) return setError('Pick when it’s next due.');
    setError(null);
    setSaving(true);
    try {
      await onSave({ name: name.trim(), every_days: Math.round(every), next_due: nextDue, note: note.trim(), assignees });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <View style={{ gap: t.spacing.md }}>
      <TextField label="Chore" value={name} onChangeText={setName} placeholder="e.g. Take out the bins" editable={editable} testID="chore-name" />
      <NumberField label="Every how many days" value={every} onChange={setEvery} min={1} decimals={0} testID="chore-every" />
      <DateField label="Next due" value={nextDue} onChange={setNextDue} testID="chore-next-due" />
      <Section title="Taking turns" footer={assignees.length ? 'In the order you picked them.' : 'Nobody picked: anyone can do it.'}>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: t.spacing.sm }}>
          {members.map((m) => {
            const at = assignees.indexOf(m.id);
            return (
              <Chip
                key={m.id}
                label={at >= 0 ? `${at + 1}. ${m.name}` : m.name}
                selected={at >= 0}
                onPress={editable ? () => toggle(m.id) : undefined}
                testID={`chore-assignee-${m.username}`}
              />
            );
          })}
        </View>
      </Section>
      <TextField label="Note" value={note} onChangeText={setNote} multiline placeholder="How to do it, where things are…" editable={editable} testID="chore-note" />
      {error ? (
        <Text role="alert" style={{ color: t.colors.danger }} testID="chore-error">
          {error}
        </Text>
      ) : null}
      {editable ? <Button title={saveTitle} onPress={save} loading={saving} testID="chore-save" /> : null}
    </View>
  );
}
