import { useEffect, useRef, useState } from 'react';
import { Text, View } from 'react-native';
import { deleteImage } from '@homeai/sdk';
import { Button, DateField, IconButton, PhotoField, Section, Select, TextField, useTheme } from '@homeai/ui';
import {
  ADDRESS_LABELS,
  DATE_LABELS,
  EMAIL_LABELS,
  PHONE_LABELS,
  WEBSITE_LABELS,
  emptyAddress,
  type Address,
  type Entry,
  type ImportantDate,
  type Person,
} from '../lib/contacts';

export type PersonValues = Omit<Person, 'id'> & { phones: Entry[]; emails: Entry[]; addresses: Address[]; websites: Entry[]; dates: ImportantDate[] };

export const EMPTY_PERSON: PersonValues = {
  name: '', nickname: '', company: '', job_title: '', relationship: '', birthday: null, notes: '', photo: null,
  phones: [], emails: [], addresses: [], websites: [], dates: [],
};

const CUSTOM = '__custom';

/** A preset label (mobile, work…) from a list, or "Custom…" and type one. */
function LabelField({ value, presets, onChange, testID }: { value: string; presets: string[]; onChange: (label: string) => void; testID: string }) {
  const [custom, setCustom] = useState(!presets.includes(value));
  const options = [...presets.map((p) => ({ label: p[0].toUpperCase() + p.slice(1), value: p })), { label: 'Custom…', value: CUSTOM }];
  return (
    <View style={{ gap: 4 }}>
      <Select
        value={custom ? CUSTOM : value}
        options={options}
        onChange={(v) => {
          setCustom(v === CUSTOM);
          onChange(v === CUSTOM ? '' : v);
        }}
        testID={`${testID}-label`}
      />
      {custom ? <TextField value={value} onChangeText={onChange} placeholder="Label" testID={`${testID}-custom`} /> : null}
    </View>
  );
}

function update<T>(list: T[], i: number, patch: Partial<T>): T[] {
  return list.map((x, j) => (j === i ? { ...x, ...patch } : x));
}

/** Phones, emails or websites: a label and a value each, add and remove. */
function EntryList({ title, kind, entries, presets, placeholder, keyboardType, onChange }: {
  title: string;
  kind: string;
  entries: Entry[];
  presets: string[];
  placeholder: string;
  keyboardType?: 'phone-pad' | 'email-address' | 'url';
  onChange: (entries: Entry[]) => void;
}) {
  const t = useTheme();
  return (
    <Section title={title} testID={`${kind}-section`}>
      <View style={{ gap: t.spacing.md }}>
        {entries.map((e, i) => (
          <View key={i} style={{ flexDirection: 'row', gap: t.spacing.sm, alignItems: 'flex-start' }}>
            <View style={{ width: 120 }}>
              <LabelField value={e.label} presets={presets} onChange={(label) => onChange(update(entries, i, { label }))} testID={`${kind}-${i}`} />
            </View>
            <View style={{ flex: 1 }}>
              <TextField value={e.value} onChangeText={(value) => onChange(update(entries, i, { value }))} placeholder={placeholder} keyboardType={keyboardType} testID={`${kind}-${i}-value`} />
            </View>
            <IconButton icon="remove-circle-outline" color={t.colors.danger} accessibilityLabel={`Remove ${placeholder.toLowerCase()}`} onPress={() => onChange(entries.filter((_, j) => j !== i))} testID={`${kind}-${i}-remove`} />
          </View>
        ))}
        <Button title={`Add ${placeholder.toLowerCase()}`} variant="plain" icon="add-outline" onPress={() => onChange([...entries, { label: presets[0], value: '' }])} testID={`${kind}-add`} />
      </View>
    </Section>
  );
}

const ADDRESS_FIELDS: { key: keyof Omit<Address, 'label'>; label: string; half?: boolean }[] = [
  { key: 'street1', label: 'Street' },
  { key: 'street2', label: 'Street line 2' },
  { key: 'city', label: 'City' },
  { key: 'region', label: 'State / region', half: true },
  { key: 'postcode', label: 'Postcode', half: true },
  { key: 'country', label: 'Country' },
];

function AddressList({ addresses, onChange }: { addresses: Address[]; onChange: (addresses: Address[]) => void }) {
  const t = useTheme();
  return (
    <Section title="Addresses" testID="address-section">
      <View style={{ gap: t.spacing.lg }}>
        {addresses.map((a, i) => (
          <View key={i} style={{ gap: t.spacing.sm, paddingBottom: t.spacing.md, borderBottomWidth: 1, borderColor: t.colors.border }}>
            <View style={{ flexDirection: 'row', gap: t.spacing.sm, alignItems: 'flex-start' }}>
              <View style={{ flex: 1 }}>
                <LabelField value={a.label} presets={ADDRESS_LABELS} onChange={(label) => onChange(update(addresses, i, { label }))} testID={`address-${i}`} />
              </View>
              <IconButton icon="remove-circle-outline" color={t.colors.danger} accessibilityLabel="Remove address" onPress={() => onChange(addresses.filter((_, j) => j !== i))} testID={`address-${i}-remove`} />
            </View>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: t.spacing.sm }}>
              {ADDRESS_FIELDS.map((f) => (
                <View key={f.key} style={{ flexBasis: f.half ? '45%' : '100%', flexGrow: 1 }}>
                  <TextField label={f.label} value={a[f.key]} onChangeText={(v) => onChange(update(addresses, i, { [f.key]: v } as Partial<Address>))} testID={`address-${i}-${f.key}`} />
                </View>
              ))}
            </View>
          </View>
        ))}
        <Button title="Add address" variant="plain" icon="add-outline" onPress={() => onChange([...addresses, emptyAddress()])} testID="address-add" />
      </View>
    </Section>
  );
}

function DateList({ dates, onChange }: { dates: ImportantDate[]; onChange: (dates: ImportantDate[]) => void }) {
  const t = useTheme();
  return (
    <View style={{ gap: t.spacing.md }}>
      {dates.map((d, i) => (
        <View key={i} style={{ flexDirection: 'row', gap: t.spacing.sm, alignItems: 'flex-start' }}>
          <View style={{ width: 140 }}>
            <LabelField value={d.label} presets={DATE_LABELS} onChange={(label) => onChange(update(dates, i, { label }))} testID={`date-${i}`} />
          </View>
          <View style={{ flex: 1 }}>
            <DateField value={d.date} onChange={(date) => onChange(update(dates, i, { date }))} testID={`date-${i}-date`} />
          </View>
          <IconButton icon="remove-circle-outline" color={t.colors.danger} accessibilityLabel="Remove date" onPress={() => onChange(dates.filter((_, j) => j !== i))} testID={`date-${i}-remove`} />
        </View>
      ))}
      <Button title="Add a date" variant="plain" icon="add-outline" onPress={() => onChange([...dates, { label: DATE_LABELS[0], date: null }])} testID="date-add" />
    </View>
  );
}

/**
 * Everything about a person, for New and Edit. A photo picked here and not saved is deleted
 * when the form goes away; a replaced or removed one once the save succeeds.
 */
export function PersonForm({ initial, saveTitle, onSave }: { initial: PersonValues; saveTitle: string; onSave: (v: PersonValues) => Promise<void> }) {
  const t = useTheme();
  const [v, setV] = useState<PersonValues>(initial);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const unsaved = useRef<string | null>(null);
  const set = <K extends keyof PersonValues>(key: K) => (value: PersonValues[K]) => setV((cur) => ({ ...cur, [key]: value }));

  useEffect(
    () => () => {
      if (unsaved.current) void deleteImage(unsaved.current).catch(() => {});
    },
    [],
  );

  function setPhoto(photo: string | null) {
    if (unsaved.current && unsaved.current !== photo) void deleteImage(unsaved.current).catch(() => {});
    unsaved.current = photo && photo !== initial.photo ? photo : null;
    set('photo')(photo);
  }

  async function save() {
    if (!v.name.trim()) return setError('Give them a name.');
    setError(null);
    setSaving(true);
    try {
      await onSave(v);
      unsaved.current = null;
      if (initial.photo && initial.photo !== v.photo) void deleteImage(initial.photo).catch(() => {});
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <View style={{ gap: t.spacing.lg }}>
      <PhotoField value={v.photo} onChange={setPhoto} round icon="person-outline" testID="person-photo" />
      <View style={{ gap: t.spacing.md }}>
        <TextField label="Name" value={v.name} onChangeText={set('name')} placeholder="e.g. Priya Shah" testID="person-name" />
        <TextField label="Nickname" value={v.nickname} onChangeText={set('nickname')} testID="person-nickname" />
        <TextField label="Relationship" value={v.relationship} onChangeText={set('relationship')} placeholder="e.g. sister, dentist, neighbour" testID="person-relationship" />
        <TextField label="Company" value={v.company} onChangeText={set('company')} testID="person-company" />
        <TextField label="Job title" value={v.job_title} onChangeText={set('job_title')} testID="person-job-title" />
      </View>
      <EntryList title="Phones" kind="phone" entries={v.phones} presets={PHONE_LABELS} placeholder="Phone" keyboardType="phone-pad" onChange={set('phones')} />
      <EntryList title="Emails" kind="email" entries={v.emails} presets={EMAIL_LABELS} placeholder="Email" keyboardType="email-address" onChange={set('emails')} />
      <AddressList addresses={v.addresses} onChange={set('addresses')} />
      <EntryList title="Websites" kind="website" entries={v.websites} presets={WEBSITE_LABELS} placeholder="Website" keyboardType="url" onChange={set('websites')} />
      <Section title="Dates">
        <View style={{ gap: t.spacing.md }}>
          <DateField label="Birthday" value={v.birthday} onChange={set('birthday')} hint="Leave empty if you don't know it" testID="person-birthday" />
          <DateList dates={v.dates} onChange={set('dates')} />
        </View>
      </Section>
      <TextField label="Notes" value={v.notes} onChangeText={set('notes')} multiline testID="person-notes" />
      {error ? (
        <Text role="alert" style={{ color: t.colors.danger }} testID="person-error">
          {error}
        </Text>
      ) : null}
      <Button title={saveTitle} onPress={save} loading={saving} testID="person-save" />
    </View>
  );
}
