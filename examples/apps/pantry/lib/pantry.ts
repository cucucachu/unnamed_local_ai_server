// Dates are local YYYY-MM-DD strings, compared as text.

export const LOCATIONS = ['Pantry', 'Fridge', 'Freezer'] as const;
export const SOON_DAYS = 7;

export type Item = {
  id: number;
  name: string;
  quantity: number;
  unit: string;
  location: string;
  expires_on: string | null;
  note: string;
};

export const ITEM_COLUMNS = 'id, name, quantity, unit, location, expires_on, note';

export function today(): string {
  return localDate(new Date());
}

export function localDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function inDays(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return localDate(d);
}

function dayNumber(day: string): number {
  const [y, m, d] = day.split('-').map(Number);
  return Date.UTC(y, m - 1, d) / 86_400_000;
}

/** Days until it expires (negative once expired), or null if it keeps. */
export function daysLeft(item: Pick<Item, 'expires_on'>, now = today()): number | null {
  return item.expires_on ? dayNumber(item.expires_on) - dayNumber(now) : null;
}

export function expiryLabel(item: Pick<Item, 'expires_on'>): string | null {
  const days = daysLeft(item);
  if (days === null) return null;
  if (days < 0) return `Expired ${-days} day${days === -1 ? '' : 's'} ago`;
  if (days === 0) return 'Expires today';
  if (days === 1) return 'Expires tomorrow';
  return `Expires in ${days} days`;
}

/** "2 cans", "0.5 kg", "3". */
export function amount(item: Pick<Item, 'quantity' | 'unit'>): string {
  const n = Number.isInteger(item.quantity) ? String(item.quantity) : String(Math.round(item.quantity * 100) / 100);
  return item.unit ? `${n} ${item.unit}` : n;
}
