import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'expo-router';
import { search, useQuery } from '@homeai/sdk';
import { EmptyState, List, ListRow, Photo, Screen, SearchField, Section } from '@homeai/ui';
import { SOON_DAYS, birthdayLabel, dateLabel, daysUntilBirthday, localDate, workLine, type Person } from '../lib/contacts';

type Row = Person & { reach: string | null };
type DateRow = { id: number; person_id: number; label: string; date: string; name: string };

export default function ContactsScreen() {
  const router = useRouter();
  const today = localDate(new Date());
  const { data } = useQuery<Row>(
    `SELECT p.id, p.name, p.nickname, p.company, p.job_title, p.relationship, p.birthday, p.notes, p.photo,
       (SELECT value FROM contact_details d WHERE d.person_id = p.id AND d.kind <> 'address' ORDER BY d.kind <> 'phone', d.id LIMIT 1) AS reach
     FROM people p ORDER BY p.name COLLATE NOCASE`,
  );
  const { data: dates } = useQuery<DateRow>('SELECT d.id, d.person_id, d.label, d.date, p.name FROM dates d JOIN people p ON p.id = d.person_id');
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<number[] | null>(null);

  // By meaning and by words, over names, nicknames, work, relationship and notes (homeai.index).
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
  const soon = [
    ...all
      .filter((p): p is Row & { birthday: string } => !!p.birthday)
      .map((p) => ({ key: `birthday-${p.id}`, personId: p.id, name: p.name, day: p.birthday, subtitle: birthdayLabel(p.birthday, today), icon: 'gift-outline' as const })),
    ...(dates ?? []).map((d) => ({ key: `date-${d.id}`, personId: d.person_id, name: d.name, day: d.date, subtitle: dateLabel(d.label, d.date, today), icon: 'calendar-outline' as const })),
  ]
    .filter((x) => daysUntilBirthday(x.day, today) <= SOON_DAYS)
    .sort((a, b) => daysUntilBirthday(a.day, today) - daysUntilBirthday(b.day, today));
  const open = (id: number) => router.push({ pathname: '/person/[id]', params: { id: String(id) } });

  return (
    <Screen>
      <SearchField value={query} onChangeText={setQuery} placeholder="Search people, work and notes" testID="search" />
      {!query && soon.length ? (
        <Section title="Coming up" testID="birthdays">
          <List>
            {soon.map((x) => (
              <ListRow key={x.key} title={x.name} subtitle={x.subtitle} icon={x.icon} onPress={() => open(x.personId)} testID={x.key} />
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
        renderItem={(p) => (
          <ListRow
            title={p.nickname ? `${p.name} (${p.nickname})` : p.name}
            subtitle={workLine(p) || p.reach || undefined}
            left={<Photo id={p.photo} size={40} round icon="person-outline" />}
            onPress={() => open(p.id)}
            testID={`person-${p.id}`}
          />
        )}
        testID="people"
      />
    </Screen>
  );
}
