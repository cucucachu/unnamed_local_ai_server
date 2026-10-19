import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'expo-router';
import { search, useQuery } from '@homeai/sdk';
import { EmptyState, List, ListRow, Screen, SearchField, Section } from '@homeai/ui';
import { SOON_DAYS, birthdayLabel, daysUntilBirthday, localDate, type Person } from '../lib/contacts';

type Row = Person & { reach: string | null };

export default function ContactsScreen() {
  const router = useRouter();
  const today = localDate(new Date());
  const { data } = useQuery<Row>(
    `SELECT p.id, p.name, p.birthday, p.notes,
       (SELECT value FROM contact_details d WHERE d.person_id = p.id ORDER BY d.kind <> 'phone', d.id LIMIT 1) AS reach
     FROM people p ORDER BY p.name COLLATE NOCASE`,
  );
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<number[] | null>(null);

  // By meaning and by words, over names and notes (homeai.index).
  useEffect(() => {
    if (!query.trim()) return setFound(null);
    let live = true;
    const timer = setTimeout(() => {
      search(query, 50).then(
        (hits) => live && setFound(hits.filter((h) => h.table === 'people').map((h) => h.id)),
        () => live && setFound([]),
      );
    }, 300);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [query]);

  const all = data ?? [];
  const shown = useMemo(() => (found ? found.map((id) => all.find((p) => p.id === id)).filter((p): p is Row => !!p) : all), [all, found]);
  const soon = all
    .filter((p): p is Row & { birthday: string } => !!p.birthday && daysUntilBirthday(p.birthday, today) <= SOON_DAYS)
    .sort((a, b) => daysUntilBirthday(a.birthday, today) - daysUntilBirthday(b.birthday, today));
  const open = (id: number) => router.push({ pathname: '/person/[id]', params: { id: String(id) } });

  return (
    <Screen>
      <SearchField value={query} onChangeText={setQuery} placeholder="Search people and notes" testID="search" />
      {!query && soon.length ? (
        <Section title="Birthdays coming up" testID="birthdays">
          <List>
            {soon.map((p) => (
              <ListRow key={p.id} title={p.name} subtitle={birthdayLabel(p.birthday, today)} icon="gift-outline" onPress={() => open(p.id)} testID={`birthday-${p.id}`} />
            ))}
          </List>
        </Section>
      ) : null}
      <List
        data={shown}
        keyExtractor={(p) => p.id}
        empty={
          query ? (
            <EmptyState icon="search-outline" title="Nobody found" testID="search-empty" />
          ) : (
            <EmptyState icon="people-outline" title="No contacts yet" message="Add the people you keep in touch with." testID="people-empty" />
          )
        }
        renderItem={(p) => <ListRow title={p.name} subtitle={p.reach ?? undefined} onPress={() => open(p.id)} testID={`person-${p.id}`} />}
        testID="people"
      />
    </Screen>
  );
}
