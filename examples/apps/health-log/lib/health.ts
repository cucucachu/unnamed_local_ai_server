// Dates are local YYYY-MM-DD strings.

export type Summary = 'last' | 'sum' | 'average';
export type Measure = { id: number; name: string; unit: string; goal: number | null; decimals: number; summary: Summary };

export const MEASURE_COLUMNS = 'id, name, unit, goal, decimals, summary';
export const SUMMARIES: { value: Summary; label: string }[] = [
  { value: 'last', label: 'The last reading (weight)' },
  { value: 'sum', label: 'Add them up (water, steps)' },
  { value: 'average', label: 'The average (heart rate)' },
];

/**
 * One row per measure and day: `value` is that day's entries combined by the
 * measure's `summary`. Use it as a CTE: `WITH daily AS (${DAILY}) SELECT …`.
 */
export const DAILY = `SELECT e.measure_id, e.day,
    CASE m.summary
      WHEN 'sum' THEN sum(e.value)
      WHEN 'average' THEN avg(e.value)
      ELSE (SELECT x.value FROM entries x WHERE x.measure_id = e.measure_id AND x.day = e.day ORDER BY x.id DESC LIMIT 1)
    END AS value
  FROM entries e JOIN measures m ON m.id = e.measure_id
  GROUP BY e.measure_id, e.day`;

export function localDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function today(): string {
  return localDate(new Date());
}

export function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return localDate(d);
}

/** "72.4 kg". */
export function format(value: number, m: Pick<Measure, 'unit' | 'decimals'>): string {
  const n = value.toFixed(m.decimals).replace(/\.0+$/, '');
  return m.unit ? `${n} ${m.unit}` : n;
}

/** "today", "yesterday", "Oct 12". */
export function whenLabel(day: string): string {
  if (day === today()) return 'today';
  if (day === daysAgo(1)) return 'yesterday';
  const [y, m, d] = day.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
