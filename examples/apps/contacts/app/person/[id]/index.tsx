import { useState } from 'react';
import { Text, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { deleteImage, runAction, useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { Button, Card, IconButton, List, ListRow, Photo, Screen, Section, Select, useTheme } from '@homeai/ui';
import { birthdayLabel, dateLabel, formatAddress, localDate, workLine } from '../../../lib/contacts';
import { usePerson } from '../../../lib/usePerson';

export default function PersonScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const { id } = useLocalSearchParams<{ id: string }>();
  const personId = Number(id);
  const { person, details, addresses, websites, dates, loading } = usePerson(personId);
  const { data: others } = useQuery<{ id: number; name: string; photo: string | null }>(
    'SELECT id, name, photo FROM people WHERE id <> ? ORDER BY name COLLATE NOCASE',
    [personId],
  );
  const [merge, setMerge] = useState<number | null>(null);
  const [message, setMessage] = useState('');

  if (!person) {
    return (
      <Screen>
        <Text testID="person-missing">{loading ? 'Loading…' : 'This contact was deleted.'}</Text>
      </Screen>
    );
  }

  async function mergeIn() {
    if (merge === null) return;
    const other = others?.find((o) => o.id === merge);
    await runAction('mergePeople', { keep_id: personId, drop_id: merge });
    // Their photo only moves over when this contact has none.
    if (other?.photo && person!.photo && other.photo !== person!.photo) void deleteImage(other.photo).catch(() => {});
    setMerge(null);
    setMessage(`Merged ${other?.name ?? 'them'} into ${person!.name}.`);
  }

  async function remove() {
    await db.runAsync('DELETE FROM people WHERE id = ?', [personId]);
    if (person!.photo) void deleteImage(person!.photo).catch(() => {});
    router.back();
  }

  const today = localDate(new Date());
  const phones = details.filter((d) => d.kind === 'phone');
  const emails = details.filter((d) => d.kind === 'email');
  const allAddresses = [
    ...addresses.map((a) => ({ key: `a${a.id}`, label: a.label, text: formatAddress(a) })),
    ...details.filter((d) => d.kind === 'address').map((d) => ({ key: `d${d.id}`, label: d.label, text: d.value })),
  ];
  const work = workLine(person);
  const muted = { color: t.colors.textMuted, fontSize: t.font.small };
  const nothing = !phones.length && !emails.length && !allAddresses.length && !websites.length;

  return (
    <Screen>
      <Stack.Screen
        options={{
          title: person.name,
          headerRight: () =>
            canEdit ? <IconButton icon="create-outline" accessibilityLabel="Edit contact" onPress={() => router.push({ pathname: '/person/[id]/edit', params: { id: String(personId) } })} testID="person-edit" /> : null,
        }}
      />
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: t.spacing.lg }}>
        <Photo id={person.photo} size={88} round icon="person-outline" testID="person-photo" />
        <View style={{ flex: 1, gap: 2 }}>
          <Text style={{ color: t.colors.text, fontSize: t.font.large, fontWeight: '700' }} testID="person-title">
            {person.name}
          </Text>
          {person.nickname ? <Text style={muted} testID="person-nickname">“{person.nickname}”</Text> : null}
          {work ? <Text style={{ color: t.colors.text }} testID="person-work">{work}</Text> : null}
          {person.relationship ? <Text style={muted} testID="person-relationship">{person.relationship}</Text> : null}
        </View>
      </View>
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
      {nothing ? <Text style={{ color: t.colors.textMuted }} testID="person-no-details">No phone, email or address yet.</Text> : null}
      {phones.length ? (
        <Section title="Phone">
          <List data={phones} keyExtractor={(d) => d.id} renderItem={(d) => <ListRow title={d.value} subtitle={d.label || undefined} icon="call-outline" testID={`detail-${d.id}`} />} testID="phones" />
        </Section>
      ) : null}
      {emails.length ? (
        <Section title="Email">
          <List data={emails} keyExtractor={(d) => d.id} renderItem={(d) => <ListRow title={d.value} subtitle={d.label || undefined} icon="mail-outline" testID={`detail-${d.id}`} />} testID="emails" />
        </Section>
      ) : null}
      {allAddresses.length ? (
        <Section title="Address" footer="Press and hold an address to copy it.">
          <View style={{ gap: t.spacing.sm }}>
            {allAddresses.map((a) => (
              <Card key={a.key} testID={`address-${a.key}`}>
                {a.label ? <Text style={[muted, { marginBottom: 4 }]}>{a.label}</Text> : null}
                <Text selectable style={{ color: t.colors.text, fontSize: t.font.body }} testID={`address-${a.key}-text`}>
                  {a.text}
                </Text>
              </Card>
            ))}
          </View>
        </Section>
      ) : null}
      {websites.length ? (
        <Section title="Website">
          <List data={websites} keyExtractor={(w) => w.id} renderItem={(w) => <ListRow title={w.url} subtitle={w.label || undefined} icon="globe-outline" testID={`website-${w.id}`} />} testID="websites" />
        </Section>
      ) : null}
      {dates.length ? (
        <Section title="Dates">
          <List data={dates} keyExtractor={(d) => d.id} renderItem={(d) => <ListRow title={d.date} subtitle={dateLabel(d.label, d.date, today)} icon="calendar-outline" testID={`date-${d.id}`} />} testID="dates" />
        </Section>
      ) : null}
      {person.notes ? (
        <Section title="Notes">
          <Text selectable style={{ color: t.colors.text }} testID="person-notes-text">
            {person.notes}
          </Text>
        </Section>
      ) : null}
      {canEdit ? (
        <>
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
      ) : null}
    </Screen>
  );
}
