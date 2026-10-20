import { useQuery } from '@homeai/sdk';
import { PERSON_COLUMNS, type Address, type Detail, type Person } from './contacts';

export type Website = { id: number; label: string; url: string };
export type DateRow = { id: number; label: string; date: string };
export type AddressRow = Address & { id: number };

/** One person and everything about them, live. `person` is undefined while loading or once deleted. */
export function usePerson(personId: number) {
  const people = useQuery<Person>(`SELECT ${PERSON_COLUMNS} FROM people WHERE id = ?`, [personId]);
  const details = useQuery<Detail>('SELECT id, kind, label, value FROM contact_details WHERE person_id = ? ORDER BY id', [personId]);
  const addresses = useQuery<AddressRow>(
    'SELECT id, label, street1, street2, city, region, postcode, country FROM addresses WHERE person_id = ? ORDER BY id',
    [personId],
  );
  const websites = useQuery<Website>('SELECT id, label, url FROM websites WHERE person_id = ? ORDER BY id', [personId]);
  const dates = useQuery<DateRow>('SELECT id, label, date FROM dates WHERE person_id = ? ORDER BY id', [personId]);
  const all = [people, details, addresses, websites, dates];
  return {
    person: people.data?.[0],
    details: details.data ?? [],
    addresses: addresses.data ?? [],
    websites: websites.data ?? [],
    dates: dates.data ?? [],
    loading: all.some((q) => q.data === undefined),
  };
}
