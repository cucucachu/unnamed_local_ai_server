import { useState } from 'react';
import { Text, View } from 'react-native';
import { Button, Chip, DateField, NumberField, TextField, Toggle, useTheme } from '@homeai/ui';
import { LOCATIONS, type Item } from '../lib/pantry';

export type ItemValues = Omit<Item, 'id'>;

/** The fields of a pantry item. Any location can be typed; the usual three are chips. */
export function ItemForm({ initial, saveTitle, onSave, editable = true }: { initial: ItemValues; saveTitle: string; onSave: (v: ItemValues) => Promise<void>; editable?: boolean }) {
  const t = useTheme();
  const [name, setName] = useState(initial.name);
  const [quantity, setQuantity] = useState<number | null>(initial.quantity);
  const [unit, setUnit] = useState(initial.unit);
  const [location, setLocation] = useState(initial.location);
  const [expires, setExpires] = useState(initial.expires_on !== null);
  const [expiresOn, setExpiresOn] = useState<string | null>(initial.expires_on);
  const [note, setNote] = useState(initial.note);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!name.trim()) return setError('What is it?');
    if (quantity === null || quantity < 0) return setError('How much: 0 or more.');
    if (!location.trim()) return setError('Where is it kept?');
    if (expires && !expiresOn) return setError('Pick the expiry date, or turn it off.');
    setError(null);
    setSaving(true);
    try {
      await onSave({ name: name.trim(), quantity, unit: unit.trim(), location: location.trim(), expires_on: expires ? expiresOn : null, note: note.trim() });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <View style={{ gap: t.spacing.md }}>
      <TextField label="What" value={name} onChangeText={setName} placeholder="e.g. Basmati rice" editable={editable} testID="item-name" />
      <View style={{ flexDirection: 'row', gap: t.spacing.md }}>
        <View style={{ flex: 1 }}>
          <NumberField label="How much" value={quantity} onChange={setQuantity} min={0} decimals={2} testID="item-quantity" />
        </View>
        <View style={{ flex: 1 }}>
          <TextField label="Unit" value={unit} onChangeText={setUnit} placeholder="kg, cans…" editable={editable} testID="item-unit" />
        </View>
      </View>
      <TextField label="Where" value={location} onChangeText={setLocation} editable={editable} testID="item-location" />
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: t.spacing.sm }}>
        {LOCATIONS.map((l) => (
          <Chip key={l} label={l} selected={location === l} onPress={editable ? () => setLocation(l) : undefined} testID={`item-location-${l}`} />
        ))}
      </View>
      <Toggle label="It expires" value={expires} onValueChange={setExpires} disabled={!editable} testID="item-expires" />
      {expires ? <DateField label="Best before" value={expiresOn} onChange={setExpiresOn} testID="item-expires-on" /> : null}
      <TextField label="Note" value={note} onChangeText={setNote} multiline placeholder="Brand, which shelf…" editable={editable} testID="item-note" />
      {error ? (
        <Text role="alert" style={{ color: t.colors.danger }} testID="item-error">
          {error}
        </Text>
      ) : null}
      {editable ? <Button title={saveTitle} onPress={save} loading={saving} testID="item-save" /> : null}
    </View>
  );
}
