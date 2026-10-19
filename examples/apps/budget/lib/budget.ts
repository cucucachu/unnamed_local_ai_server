// Dates are local YYYY-MM-DD strings; a month is YYYY-MM.

/** The currency amounts are shown in. Change it here for another one. */
export const CURRENCY = 'USD';

export type Category = { id: number; name: string; monthly_budget: number | null; is_income: number };
export type Transaction = { id: number; day: string; description: string; amount: number; category_id: number | null; note: string };

export const TRANSACTION_COLUMNS = 'id, day, description, amount, category_id, note';

export function localDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function today(): string {
  return localDate(new Date());
}

export function thisMonth(): string {
  return today().slice(0, 7);
}

export function addMonths(month: string, n: number): string {
  const [y, m] = month.split('-').map(Number);
  const d = new Date(y, m - 1 + n, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

/** "October 2026"; `short` gives "Oct". */
export function monthLabel(month: string, short = false): string {
  const [y, m] = month.split('-').map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, short ? { month: 'short' } : { month: 'long', year: 'numeric' });
}

/** The first and last day of a month, for `day BETWEEN ? AND ?`. */
export function monthRange(month: string): [string, string] {
  return [`${month}-01`, `${month}-31`];
}

/**
 * A rule pattern for a bank description: the description without the store
 * numbers and reference codes that change between visits, so
 * "TRADER JOE'S #552 0412" suggests "TRADER JOE'S".
 */
export function suggestPattern(description: string): string {
  const words = description.trim().split(/\s+/);
  while (words.length > 1 && /[#\d*]/.test(words[words.length - 1])) words.pop();
  return words.join(' ');
}
