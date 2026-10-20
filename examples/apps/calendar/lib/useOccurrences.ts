import { useQuery } from '@homeai/sdk';
import { EVENT_SELECT, birthdaysIn, compareOccurrences, occurrencesOf, type CalendarEvent, type Occurrence } from './calendar';

type Person = { _instance: string; id: number; name: string; birthday: string };

// Contacts' people (homeai.reads in app.json). contacts_people only exists
// while a Contacts copy is attached, so ask homeai_exports first.
const NO_PEOPLE = 'SELECT NULL AS _instance, NULL AS id, NULL AS name, NULL AS birthday WHERE 0';
const PEOPLE = "SELECT p._instance, p.id, p.name, p.birthday FROM contacts_people p WHERE p.birthday IS NOT NULL AND p.birthday <> ''";

/**
 * Everything on the calendar from..to: event occurrences, then Contacts
 * birthdays, sorted. With `person` (a user id), only events for them, and
 * no birthdays. Re-reads whenever anyone changes the database.
 */
export function useOccurrences(from: string, to: string, person: string | null): { occurrences: Occurrence[]; loading: boolean } {
  const { data: events, loading } = useQuery<CalendarEvent>(`${EVENT_SELECT} WHERE e.start_date <= ? AND (e.end_date >= ? OR e.repeat <> 'none')`, [to, from]);
  const { data: copies } = useQuery<{ n: number }>("SELECT count(*) AS n FROM homeai_exports WHERE app = 'contacts' AND export = 'people'");
  const hasContacts = (copies?.[0]?.n ?? 0) > 0;
  const { data: people } = useQuery<Person>(hasContacts ? PEOPLE : NO_PEOPLE);

  const occurrences = [
    ...(events ?? [])
      .filter((e) => !person || (e.people ?? '').split(',').includes(person))
      .flatMap((e) => occurrencesOf(e, from, to)),
    ...(person ? [] : (people ?? []).filter((p) => /^\d{4}-\d{2}-\d{2}$/.test(p.birthday)).flatMap((p) => birthdaysIn(p, from, to))),
  ].sort(compareOccurrences);
  return { occurrences, loading };
}
