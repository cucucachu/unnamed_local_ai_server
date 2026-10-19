// Dates are local YYYY-MM-DD strings.

export type Recipe = {
  id: number;
  name: string;
  cuisine: string;
  servings: number;
  minutes: number | null;
  ingredients: string;
  steps: string;
};

export const RECIPE_COLUMNS = 'id, name, cuisine, servings, minutes, ingredients, steps';
export const SLOTS = ['Breakfast', 'Lunch', 'Dinner'] as const;

export function localDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function parseDay(day: string): Date {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(y, m - 1, d);
}

export function addDays(day: string, n: number): string {
  const d = parseDay(day);
  d.setDate(d.getDate() + n);
  return localDate(d);
}

/** The Monday on or before `day`. */
export function weekStart(day: string): string {
  return addDays(day, -((parseDay(day).getDay() + 6) % 7));
}

/** "Mon 19 Oct". */
export function dayLabel(day: string): string {
  return parseDay(day).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
}

/** The ingredient lines, without blank lines or list markers ("- ", "* ", "• "). */
export function ingredientLines(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.replace(/^\s*(?:[-*•]|\d+[.)])\s+/, '').trim())
    .filter(Boolean);
}

export function summary(r: Pick<Recipe, 'cuisine' | 'servings' | 'minutes'>): string {
  return [r.cuisine, `serves ${r.servings}`, r.minutes ? `${r.minutes} min` : null].filter(Boolean).join(' · ');
}
