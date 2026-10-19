import { useState } from 'react';
import { Text, View } from 'react-native';
import { useQuery } from '@homeai/sdk';
import { Button, DateField, NumberField, Select, TextField, Toggle, useTheme } from '@homeai/ui';
import { suggestPattern, type Category, type Transaction } from '../lib/budget';

export type TransactionValues = Omit<Transaction, 'id'>;

/**
 * The fields of a transaction. The amount is typed as a positive number and
 * "Money in" sets its sign. `pattern` is the rule to remember, or null.
 */
export function TransactionForm({
  initial,
  saveTitle,
  onSave,
  editable = true,
}: {
  initial: TransactionValues;
  saveTitle: string;
  onSave: (v: TransactionValues, pattern: string | null) => Promise<void>;
  editable?: boolean;
}) {
  const t = useTheme();
  const { data: categories } = useQuery<Category>('SELECT id, name, monthly_budget, is_income FROM categories ORDER BY is_income, name COLLATE NOCASE');
  const [day, setDay] = useState<string | null>(initial.day);
  const [description, setDescription] = useState(initial.description);
  const [amount, setAmount] = useState<number | null>(initial.amount ? Math.abs(initial.amount) : null);
  const [incoming, setIncoming] = useState(initial.amount > 0);
  const [category, setCategory] = useState<number | null>(initial.category_id);
  const [remember, setRemember] = useState(false);
  const [pattern, setPattern] = useState('');
  const [note, setNote] = useState(initial.note);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!day) return setError('Pick the date.');
    if (amount === null || amount <= 0) return setError('How much: more than 0.');
    const rule = remember && category !== null ? pattern.trim() : '';
    if (remember && !rule) return setError('Say what the description contains, or turn off Always use this category.');
    setError(null);
    setSaving(true);
    try {
      const signed = Math.round(amount * 100) / 100;
      await onSave({ day, description: description.trim(), amount: incoming ? signed : -signed, category_id: category, note: note.trim() }, rule || null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <View style={{ gap: t.spacing.md }}>
      <TextField label="Description" value={description} onChangeText={setDescription} placeholder="e.g. Trader Joe's" editable={editable} testID="txn-description" />
      <NumberField label="Amount" value={amount} onChange={setAmount} min={0} decimals={2} testID="txn-amount" />
      <Toggle label="Money in" description="Income or a refund, rather than spending" value={incoming} onValueChange={setIncoming} disabled={!editable} testID="txn-income" />
      <DateField label="Date" value={day} onChange={setDay} testID="txn-day" />
      <Select
        label="Category"
        value={category}
        options={(categories ?? []).map((c) => ({ label: c.name, value: c.id }))}
        onChange={setCategory}
        placeholder={categories?.length ? 'Uncategorized' : 'Add categories first'}
        testID="txn-category"
      />
      {editable && category !== null && description.trim() ? (
        <Toggle
          label="Always use this category"
          description="For other transactions with a description like this one"
          value={remember}
          onValueChange={(on) => {
            setRemember(on);
            if (on && !pattern) setPattern(suggestPattern(description));
          }}
          testID="txn-remember"
        />
      ) : null}
      {remember && category !== null && description.trim() ? (
        <TextField label="When the description contains" value={pattern} onChangeText={setPattern} hint="Any case. Leave out store numbers." testID="txn-pattern" />
      ) : null}
      <TextField label="Note" value={note} onChangeText={setNote} multiline editable={editable} testID="txn-note" />
      {error ? (
        <Text role="alert" style={{ color: t.colors.danger }} testID="txn-error">
          {error}
        </Text>
      ) : null}
      {editable ? <Button title={saveTitle} onPress={save} loading={saving} testID="txn-save" /> : null}
    </View>
  );
}
