import { useRouter } from 'expo-router';
import { runAction } from '@homeai/sdk';
import { Screen } from '@homeai/ui';
import { EMPTY_PERSON, PersonForm, type PersonValues } from '../components/PersonForm';
import { savePersonParams } from '../lib/contacts';

export default function NewPersonScreen() {
  const router = useRouter();

  async function create(v: PersonValues) {
    const { rows } = await runAction('savePerson', savePersonParams(null, v));
    router.replace({ pathname: '/person/[id]', params: { id: String(rows[0].id) } });
  }

  return (
    <Screen>
      <PersonForm initial={EMPTY_PERSON} saveTitle="Add contact" onSave={create} />
    </Screen>
  );
}
