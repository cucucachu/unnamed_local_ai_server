// Dates are local YYYY-MM-DD strings.

export type Exercise = { id: number; name: string; unit: string; note: string };
export type SetRow = { id: number; exercise_id: number; reps: number; weight: number; position: number };

export function localDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function today(): string {
  return localDate(new Date());
}

/** "Mon 19 Oct". */
export function dayLabel(day: string): string {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
}

/** "5 × 60 kg", or "12 reps" for bodyweight. */
export function setLabel(s: Pick<SetRow, 'reps' | 'weight'>, unit: string): string {
  return s.weight > 0 ? `${s.reps} × ${round(s.weight)} ${unit}`.trim() : `${s.reps} reps`;
}

export function round(n: number): number {
  return Math.round(n * 100) / 100;
}
