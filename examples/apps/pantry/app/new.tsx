import { useRouter } from 'expo-router';
import { runAction } from '@homeai/sdk';
import { Screen } from '@homeai/ui';
import { ItemForm, type ItemValues } from '../components/ItemForm';

export default function NewItemScreen() {
  const router = useRouter();

  async function add(v: ItemValues) {
    await runAction('addItem', { name: v.name, quantity: v.quantity, unit: v.unit, location: v.location, expires_on: v.expires_on, note: v.note });
    router.back();
  }

  return (
    <Screen>
      <ItemForm initial={{ name: '', quantity: 1, unit: '', location: 'Pantry', expires_on: null, note: '' }} saveTitle="Add" onSave={add} />
    </Screen>
  );
}
