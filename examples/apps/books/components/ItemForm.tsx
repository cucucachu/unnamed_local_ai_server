import { useState } from 'react';
import { Text, View } from 'react-native';
import { Button, DateField, Select, TextField, useTheme } from '@homeai/ui';
import { KINDS, STATUSES, kindOf, today, type Item, type Kind, type Status } from '../lib/books';
import { Stars } from './Stars';

export type ItemValues = Omit<Item, 'id'>;

/** The fields of an item. The dates show once it's started; changing the status fills them in. */
export function ItemForm({ initial, saveTitle, onSave, editable = true }: { initial: ItemValues; saveTitle: string; onSave: (v: ItemValues) => Promise<void>; editable?: boolean }) {
  const t = useTheme();
  const [title, setTitle] = useState(initial.title);
  const [creator, setCreator] = useState(initial.creator);
  const [kind, setKind] = useState<Kind>(initial.kind);
  const [status, setStatus] = useState<Status>(initial.status);
  const [rating, setRating] = useState<number | null>(initial.rating);
  const [startedOn, setStartedOn] = useState<string | null>(initial.started_on);
  const [finishedOn, setFinishedOn] = useState<string | null>(initial.finished_on);
  const [notes, setNotes] = useState(initial.notes);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // The same dates actions/setStatus.sql stamps; the fields can still be edited after.
  function changeStatus(next: Status) {
    setStatus(next);
    if (next === 'want') {
      setStartedOn(null);
      setFinishedOn(null);
      return;
    }
    if (next !== 'dropped' && !startedOn) setStartedOn(today());
    if (next === 'finished' || next === 'dropped') setFinishedOn((d) => d ?? today());
    else setFinishedOn(null);
  }

  async function save() {
    if (!title.trim()) return setError('Give it a title.');
    setError(null);
    setSaving(true);
    try {
      await onSave({ title: title.trim(), creator: creator.trim(), kind, status, rating, started_on: startedOn, finished_on: finishedOn, notes: notes.trim() });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <View style={{ gap: t.spacing.md }}>
      <TextField label="Title" value={title} onChangeText={setTitle} placeholder="e.g. Piranesi" editable={editable} testID="item-title" />
      <TextField label={kindOf(kind).creator} value={creator} onChangeText={setCreator} editable={editable} testID="item-creator" />
      {editable ? (
        <>
          <Select label="Kind" value={kind} options={KINDS.map((k) => ({ label: k.label, value: k.value }))} onChange={(v) => v && setKind(v)} testID="item-kind" />
          <Select label="Status" value={status} options={STATUSES.map((s) => ({ label: s.label, value: s.value }))} onChange={(v) => v && changeStatus(v)} testID="item-status" />
          {status !== 'want' ? <DateField label="Started" value={startedOn} onChange={setStartedOn} testID="item-started" /> : null}
          {status === 'finished' || status === 'dropped' ? <DateField label={status === 'finished' ? 'Finished' : 'Gave up'} value={finishedOn} onChange={setFinishedOn} testID="item-finished" /> : null}
        </>
      ) : null}
      <Stars value={rating} onChange={setRating} disabled={!editable} testID="item-rating" />
      <TextField label="Notes" value={notes} onChangeText={setNotes} placeholder="Thoughts, quotes, who recommended it" multiline editable={editable} testID="item-notes" />
      {error ? (
        <Text role="alert" style={{ color: t.colors.danger }} testID="item-error">
          {error}
        </Text>
      ) : null}
      {editable ? <Button title={saveTitle} onPress={save} loading={saving} testID="item-save" /> : null}
    </View>
  );
}
