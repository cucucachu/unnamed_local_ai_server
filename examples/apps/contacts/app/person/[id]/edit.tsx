import { Text } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { runAction } from '@homeai/sdk';
import { Screen } from '@homeai/ui';
import { PersonForm, type PersonValues } from '../../../components/PersonForm';
import { emptyAddress, savePersonParams } from '../../../lib/contacts';
import { usePerson } from '../../../lib/usePerson';

export default function EditPersonScreen() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const personId = Number(id);
  const { person, details, addresses, websites, dates, loading } = usePerson(personId);

  if (!person || loading) {
    return (
      <Screen>
        <Text testID="person-missing">{loading ? 'Loading…' : 'This contact was deleted.'}</Text>
      </Screen>
    );
  }

  const { id: _id, ...fields } = person;
  const initial: PersonValues = {
    ...fields,
    phones: details.filter((d) => d.kind === 'phone').map((d) => ({ label: d.label, value: d.value })),
    emails: details.filter((d) => d.kind === 'email').map((d) => ({ label: d.label, value: d.value })),
    // One-line addresses from before `addresses` become street line 1 and are saved as parts.
    addresses: [
      ...addresses.map(({ id: _a, ...a }) => a),
      ...details.filter((d) => d.kind === 'address').map((d) => ({ ...emptyAddress(d.label), street1: d.value })),
    ],
    websites: websites.map((w) => ({ label: w.label, value: w.url })),
    dates: dates.map((d) => ({ label: d.label, date: d.date })),
  };

  async function save(v: PersonValues) {
    await runAction('savePerson', savePersonParams(personId, v));
    router.back();
  }

  return (
    <Screen>
      <PersonForm key={person.id} initial={initial} saveTitle="Save" onSave={save} />
    </Screen>
  );
}
