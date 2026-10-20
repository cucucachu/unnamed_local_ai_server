// Dates are local YYYY-MM-DD strings.

export type Person = {
  id: number;
  name: string;
  nickname: string;
  company: string;
  job_title: string;
  relationship: string;
  birthday: string | null;
  notes: string;
  photo: string | null;
};
export const PERSON_COLUMNS = 'id, name, nickname, company, job_title, relationship, birthday, notes, photo';

export type Kind = 'phone' | 'email' | 'address';
/** A `contact_details` row: a phone or email (or a one-line address from before `addresses`). */
export type Detail = { id: number; kind: Kind; label: string; value: string };
export type Entry = { label: string; value: string };
export type Address = { label: string; street1: string; street2: string; city: string; region: string; postcode: string; country: string };
export type ImportantDate = { label: string; date: string | null };

export const PHONE_LABELS = ['mobile', 'home', 'work', 'other'];
export const EMAIL_LABELS = ['home', 'work', 'other'];
export const ADDRESS_LABELS = ['home', 'work', 'other'];
export const WEBSITE_LABELS = ['personal', 'work', 'other'];
export const DATE_LABELS = ['anniversary', 'other'];

export const emptyAddress = (label = 'home'): Address => ({ label, street1: '', street2: '', city: '', region: '', postcode: '', country: '' });

/** The address as lines: street, street, "City, Region Postcode", country. */
export function formatAddress(a: Omit<Address, 'label'>): string {
  const town = [[a.city, a.region].filter(Boolean).join(', '), a.postcode].filter(Boolean).join(' ');
  return [a.street1, a.street2, town, a.country].map((line) => line.trim()).filter(Boolean).join('\n');
}

/** "Job title · Company", or whichever is known. */
export function workLine(p: Pick<Person, 'job_title' | 'company'>): string {
  return [p.job_title, p.company].filter(Boolean).join(' · ');
}

/** `savePerson`'s params: the person, plus their lists as JSON. Blank entries are dropped. */
export function savePersonParams(
  id: number | null,
  v: Omit<Person, 'id'> & { phones: Entry[]; emails: Entry[]; addresses: Address[]; websites: Entry[]; dates: ImportantDate[] },
) {
  const kept = (entries: Entry[], kind: 'phone' | 'email') =>
    entries.filter((e) => e.value.trim()).map((e) => ({ kind, label: e.label.trim(), value: e.value.trim() }));
  const trimAll = <T extends Record<string, unknown>>(o: T) =>
    Object.fromEntries(Object.entries(o).map(([k, x]) => [k, typeof x === 'string' ? x.trim() : x])) as T;
  return {
    id,
    name: v.name.trim(),
    nickname: v.nickname.trim(),
    company: v.company.trim(),
    job_title: v.job_title.trim(),
    relationship: v.relationship.trim(),
    birthday: v.birthday,
    notes: v.notes.trim(),
    photo: v.photo,
    details: JSON.stringify([...kept(v.phones, 'phone'), ...kept(v.emails, 'email')]),
    addresses: JSON.stringify(v.addresses.map(trimAll).filter((a) => formatAddress(a))),
    websites: JSON.stringify(v.websites.filter((w) => w.value.trim()).map((w) => ({ label: w.label.trim(), url: w.value.trim() }))),
    dates: JSON.stringify(v.dates.filter((d) => d.date).map((d) => ({ label: d.label.trim(), date: d.date }))),
  };
}

export const SOON_DAYS = 30;

function parts(day: string): [number, number, number] {
  const [y, m, d] = day.split('-').map(Number);
  return [y, m, d];
}

function isLeap(y: number): boolean {
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
}

/** Days from `today` to the next birthday (0 = today). Feb 29 falls on Feb 28 in other years. */
export function daysUntilBirthday(birthday: string, today: string): number {
  const [, bm, bd] = parts(birthday);
  const [ty, tm, td] = parts(today);
  const start = new Date(ty, tm - 1, td);
  for (const year of [ty, ty + 1]) {
    const day = bm === 2 && bd === 29 && !isLeap(year) ? 28 : bd;
    const next = new Date(year, bm - 1, day);
    if (next >= start) return Math.round((next.getTime() - start.getTime()) / 86_400_000);
  }
  return 365;
}

/** The age they turn on their next birthday. */
export function turning(birthday: string, today: string): number {
  const [ty, tm, td] = parts(today);
  const next = new Date(ty, tm - 1, td + daysUntilBirthday(birthday, today));
  return next.getFullYear() - parts(birthday)[0];
}

function when(day: string, today: string): string {
  const n = daysUntilBirthday(day, today);
  return n === 0 ? 'Today' : n === 1 ? 'Tomorrow' : `In ${n} days`;
}

export function birthdayLabel(birthday: string, today: string): string {
  return `${when(birthday, today)} · turns ${turning(birthday, today)}`;
}

/** e.g. "Anniversary · In 3 days · 10 years". Yearly, like a birthday. */
export function dateLabel(label: string, date: string, today: string): string {
  const name = label ? label[0].toUpperCase() + label.slice(1) : 'Date';
  return `${name} · ${when(date, today)} · ${turning(date, today)} years`;
}

export function localDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
