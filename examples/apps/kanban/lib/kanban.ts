export type Board = { id: number; name: string };
export type Column = { id: number; board_id: number; name: string; position: number; wip_limit: number | null };
export type Card = { id: number; column_id: number; title: string; note: string; position: number; due: string | null; assignee: string | null };

export const CARD_COLUMNS = 'id, column_id, title, note, position, due, assignee';
export const COLUMN_COLUMNS = 'id, board_id, name, position, wip_limit';

const pad = (n: number) => String(n).padStart(2, '0');

/** Today's local date, YYYY-MM-DD. */
export function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** "Due today", "Due Oct 21", "Overdue (Oct 2)". */
export function dueLabel(due: string): string {
  const [y, m, d] = due.split('-').map(Number);
  const when = new Date(y, m - 1, d).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  const now = today();
  if (due === now) return 'Due today';
  return due < now ? `Overdue (${when})` : `Due ${when}`;
}

/** "Doing · 2 of 3", or "Doing · 2" with no limit. */
export function columnTitle(c: Pick<Column, 'name' | 'wip_limit'>, count: number): string {
  return `${c.name} · ${c.wip_limit ? `${count} of ${c.wip_limit}` : count}`;
}
