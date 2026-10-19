import { useState } from 'react';
import { Text, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { runAction, useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { Button, List, ListRow, Screen, Section, Select, TextField, useTheme } from '@homeai/ui';
import { PersonForm, type PersonValues } from '../../components/PersonForm';
import { KINDS, birthdayLabel, localDate, type Detail, type Kind, type Person } from '../../lib/contacts';

export default function PersonScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const { id } = useLocalSearchParams<{ id: string }>();
  const personId = Number(id);
  const { data, loading } = useQuery<Person>('SELECT id, name, birthday, notes FROM people WHERE id = ?', [personId]);
  const { data: details } = useQuery<Detail>('SELECT id, kind, label, value FROM contact_details WHERE person_id = ? ORDER BY kind, id', [personId]);
  const { data: others } = useQuery<{ id: number; name: string }>('SELECT id, name FROM people WHERE id <> ? ORDER BY name COLLATE NOCASE', [personId]);
  const [kind, setKind] = useState<Kind>('phone');
  const [label, setLabel] = useState('');
  const [value, setValue] = useState('');
  const [merge, setMerge] = useState<number | null>(null);
  const [message, setMessage] = useState('');
  const person = data?.[0];

  if (!person) {
    return (
      <Screen>
        <Text testID="person-missing">{loading ? 'Loading…' : 'This contact was deleted.'}</Text>
      </Screen>
    );
  }

  async function save(v: PersonValues) {
    await db.runAsync('UPDATE people SET name = ?, birthday = ?, notes = ? WHERE id = ?', [v.name, v.birthday, v.notes, personId]);
    setMessage('Saved.');
  }

  async function addDetail() {
    if (!value.trim()) return;
    await db.runAsync('INSERT INTO contact_details (person_id, kind, label, value) VALUES (?, ?, ?, ?)', [personId, kind, label.trim(), value.trim()]);
    setLabel('');
    setValue('');
  }

  async function mergeIn() {
    if (merge === null) return;
    const name = others?.find((o) => o.id === merge)?.name ?? 'them';
    await runAction('mergePeople', { keep_id: personId, drop_id: merge });
    setMerge(null);
    setMessage(`Merged ${name} into ${person!.name}.`);
  }

  async function remove() {
    await db.runAsync('DELETE FROM people WHERE id = ?', [personId]);
    router.back();
  }

  const icon = (k: Kind) => KINDS.find((x) => x.value === k)?.icon ?? 'call-outline';
  const today = localDate(new Date());

  return (
    <Screen>
      <Stack.Screen options={{ title: person.name }} />
      {person.birthday ? (
        <Text style={{ color: t.colors.textMuted }} testID="person-next-birthday">
          Birthday {person.birthday.slice(5)} · {birthdayLabel(person.birthday, today)}
        </Text>
      ) : null}
      {message ? (
        <Text style={{ color: t.colors.textMuted }} testID="person-message">
          {message}
        </Text>
      ) : null}
      <Section title="Phone, email, address">
        <List
          data={details ?? []}
          keyExtractor={(d) => d.id}
          empty={<Text style={{ color: t.colors.textMuted, padding: t.spacing.md }}>None yet.</Text>}
          renderItem={(d) => (
            <ListRow
              title={d.value}
              subtitle={d.label || undefined}
              icon={icon(d.kind)}
              actions={canEdit ? [{ label: 'Remove', icon: 'trash-outline', onPress: () => void db.runAsync('DELETE FROM contact_details WHERE id = ?', [d.id]) }] : undefined}
              testID={`detail-${d.id}`}
            />
          )}
          testID="details"
        />
      </Section>
      {canEdit ? (
        <>
          <Section title="Add a way to reach them">
            <View style={{ gap: t.spacing.sm }}>
              <Select label="Kind" value={kind} options={KINDS.map((k) => ({ label: k.label, value: k.value }))} onChange={setKind} testID="detail-kind" />
              <TextField label="Label" value={label} onChangeText={setLabel} placeholder="e.g. mobile, work" testID="detail-label" />
              <TextField label="Value" value={value} onChangeText={setValue} testID="detail-value" />
              <Button title="Add" variant="secondary" icon="add-outline" onPress={addDetail} disabled={!value.trim()} testID="detail-add" />
            </View>
          </Section>
          <Section title="Details">
            <PersonForm key={person.id} initial={person} saveTitle="Save" onSave={save} />
          </Section>
          {others?.length ? (
            <Section title="Merge a duplicate" footer="Their details and notes move here, and the other entry is deleted.">
              <View style={{ gap: t.spacing.sm }}>
                <Select
                  label="Same person as"
                  value={merge}
                  options={others.map((o) => ({ label: o.name, value: o.id }))}
                  onChange={setMerge}
                  placeholder="Pick a contact"
                  testID="merge-pick"
                />
                <Button title="Merge into this contact" variant="secondary" onPress={mergeIn} disabled={merge === null} testID="merge" />
              </View>
            </Section>
          ) : null}
          <Button title="Delete contact" variant="destructive" onPress={remove} testID="person-delete" />
        </>
      ) : person.notes ? (
        <Section title="Notes">
          <Text style={{ color: t.colors.text }}>{person.notes}</Text>
        </Section>
      ) : null}
    </Screen>
  );
}
