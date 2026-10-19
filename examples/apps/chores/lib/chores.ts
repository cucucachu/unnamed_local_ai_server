// Dates are local YYYY-MM-DD strings, compared as text.

export function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function dayNumber(day: string): number {
  const [y, m, d] = day.split('-').map(Number);
  return Date.UTC(y, m - 1, d) / 86_400_000;
}

/** "Due today", "Overdue by 2 days", "Due tomorrow", "In 5 days". */
export function dueLabel(nextDue: string, now = today()): string {
  const days = dayNumber(nextDue) - dayNumber(now);
  if (days === 0) return 'Due today';
  if (days < 0) return `Overdue by ${-days} day${days === -1 ? '' : 's'}`;
  if (days === 1) return 'Due tomorrow';
  return `In ${days} days`;
}

export function everyLabel(days: number): string {
  if (days === 1) return 'Every day';
  if (days === 7) return 'Every week';
  if (days % 7 === 0) return `Every ${days / 7} weeks`;
  return `Every ${days} days`;
}

/** Whose turn it is, with the rotation done in SQL (window functions over chore_assignees). */
export const CHORES_SQL = `
WITH rotation AS (
  SELECT chore_id, user_id,
         ROW_NUMBER() OVER (PARTITION BY chore_id ORDER BY position, id) - 1 AS n,
         COUNT(*) OVER (PARTITION BY chore_id) AS people
  FROM chore_assignees
)
SELECT c.id, c.name, c.every_days, c.next_due, c.note, r.user_id AS turn_user
FROM chores c
LEFT JOIN rotation r ON r.chore_id = c.id AND r.n = c.turn % r.people`;

export type Chore = { id: number; name: string; every_days: number; next_due: string; note: string; turn_user: string | null };
