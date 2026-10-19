export type Deck = { id: number; name: string; description: string };
export type Card = { id: number; deck_id: number; front: string; back: string; due: string; interval_days: number; ease: number; reps: number; lapses: number };

export const CARD_COLUMNS = 'id, deck_id, front, back, due, interval_days, ease, reps, lapses';

export const GRADES = [
  { grade: 0, label: 'Again' },
  { grade: 1, label: 'Hard' },
  { grade: 2, label: 'Good' },
  { grade: 3, label: 'Easy' },
] as const;

/** A card counts as learned once it's spaced three weeks or more apart. */
export const LEARNED_DAYS = 21;

const pad = (n: number) => String(n).padStart(2, '0');
const local = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

/** Today's local date, YYYY-MM-DD. */
export function today(): string {
  return local(new Date());
}

/** The local date `n` days from today. */
export function inDays(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return local(d);
}

/** Whole days from today to `day`; negative if it's past. */
export function daysUntil(day: string): number {
  const [y, m, d] = day.split('-').map(Number);
  const now = new Date();
  return Math.round((Date.UTC(y, m - 1, d) - Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())) / 86_400_000);
}

/** The interval `grade` would give `card`: the same rules as actions/grade.sql. */
export function nextInterval(card: Pick<Card, 'interval_days' | 'ease' | 'reps'>, grade: number): number {
  const { interval_days: i, ease, reps } = card;
  if (grade === 0) return 0;
  if (grade === 1) return Math.max(1, Math.round(i * 1.2));
  if (grade === 2) return reps === 0 ? 1 : reps === 1 ? 3 : Math.max(i + 1, Math.round(i * ease));
  return reps === 0 ? 4 : Math.max(i + 1, Math.round(i * ease * 1.3));
}

/** "today", "1 day", "12 days", "2 mo", "1.5 yr". */
export function intervalLabel(days: number): string {
  if (days <= 0) return 'today';
  if (days < 30) return `${days} day${days === 1 ? '' : 's'}`;
  if (days < 365) return `${Math.round(days / 30)} mo`;
  return `${Math.round((days / 365) * 10) / 10} yr`;
}

/** When a card is due, for a list: "New", "Due", "Due in 3 days". */
export function dueLabel(card: Pick<Card, 'due' | 'reps' | 'interval_days'>): string {
  const n = daysUntil(card.due);
  if (card.reps === 0 && card.interval_days === 0 && n <= 0) return 'New';
  if (n <= 0) return 'Due';
  return `Due in ${intervalLabel(n)}`;
}
