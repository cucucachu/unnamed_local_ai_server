// Dates are local YYYY-MM-DD strings.

export type Person = { id: number; name: string; birthday: string | null; notes: string };
export type Detail = { id: number; kind: Kind; label: string; value: string };
export type Kind = 'phone' | 'email' | 'address';

export const KINDS: { value: Kind; label: string; icon: 'call-outline' | 'mail-outline' | 'home-outline' }[] = [
  { value: 'phone', label: 'Phone', icon: 'call-outline' },
  { value: 'email', label: 'Email', icon: 'mail-outline' },
  { value: 'address', label: 'Address', icon: 'home-outline' },
];

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

export function birthdayLabel(birthday: string, today: string): string {
  const n = daysUntilBirthday(birthday, today);
  const when = n === 0 ? 'Today' : n === 1 ? 'Tomorrow' : `In ${n} days`;
  return `${when} · turns ${turning(birthday, today)}`;
}

export function localDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
