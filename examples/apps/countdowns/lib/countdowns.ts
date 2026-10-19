// Dates are local YYYY-MM-DD strings.

export type Countdown = { id: number; title: string; day: string; yearly: number; note: string };
export const COUNTDOWN_COLUMNS = 'id, title, day, yearly, note';

function parts(day: string): [number, number, number] {
  const [y, m, d] = day.split('-').map(Number);
  return [y, m, d];
}

function dayNumber(y: number, m: number, d: number): number {
  return Date.UTC(y, m - 1, d) / 86_400_000;
}

function isLeap(y: number): boolean {
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
}

export function localDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function today(): string {
  return localDate(new Date());
}

/** Days from `today` to `day` (negative once it's past). */
export function daysUntil(day: string, now = today()): number {
  return dayNumber(...parts(day)) - dayNumber(...parts(now));
}

/**
 * The next time a yearly date comes round, on or after `now`, and how many
 * years it will have been. Feb 29 falls on Feb 28 in other years.
 */
export function nextYearly(day: string, now = today()): { days: number; years: number } {
  const [y, m, d] = parts(day);
  const [ny] = parts(now);
  for (const year of [ny, ny + 1]) {
    const dd = m === 2 && d === 29 && !isLeap(year) ? 28 : d;
    const days = dayNumber(year, m, dd) - dayNumber(...parts(now));
    if (days >= 0) return { days, years: year - y };
  }
  return { days: 365, years: ny + 1 - y };
}

export function daysLabel(days: number): string {
  if (days === 0) return 'Today';
  if (days === 1) return 'Tomorrow';
  if (days === -1) return 'Yesterday';
  return days > 0 ? `In ${days} days` : `${-days} days ago`;
}

/** "1st", "2nd", "11th", "23rd". */
export function ordinal(n: number): string {
  const s = n % 100 >= 11 && n % 100 <= 13 ? 'th' : ['th', 'st', 'nd', 'rd'][n % 10] ?? 'th';
  return `${n}${s}`;
}
