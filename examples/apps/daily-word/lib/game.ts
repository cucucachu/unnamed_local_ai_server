import { ANSWERS, GUESSES } from './words';

export const LENGTH = 5;
export const MAX_GUESSES = 6;
/** Day 0 of the word list: the word on day n is ANSWERS[n % ANSWERS.length]. */
const FIRST_DAY = '2026-01-01';

export type Game = { id: number; day: string; word: string; guesses: string; status: 'playing' | 'won' | 'lost'; _created_by: string };
export const GAME_COLUMNS = 'id, day, word, guesses, status, _created_by';

export type Mark = 'correct' | 'present' | 'absent';

function dayNumber(day: string): number {
  const [y, m, d] = day.split('-').map(Number);
  return Date.UTC(y, m - 1, d) / 86_400_000;
}

export function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function yesterday(day: string): string {
  return new Date((dayNumber(day) - 1) * 86_400_000).toISOString().slice(0, 10);
}

export function wordFor(day: string): string {
  const n = dayNumber(day) - dayNumber(FIRST_DAY);
  return ANSWERS[((n % ANSWERS.length) + ANSWERS.length) % ANSWERS.length];
}

export function isWord(guess: string): boolean {
  return GUESSES.has(guess);
}

export function guessesOf(g: Pick<Game, 'guesses'> | null | undefined): string[] {
  return g?.guesses ? g.guesses.split(',') : [];
}

/**
 * Each letter of a guess: in the right place, elsewhere in the word, or not
 * in it. A repeated letter is "present" only as many times as the word has
 * it left over after the right-place matches.
 */
export function mark(guess: string, word: string): Mark[] {
  const marks: Mark[] = Array(LENGTH).fill('absent');
  const left: Record<string, number> = {};
  for (let i = 0; i < LENGTH; i++) {
    if (guess[i] === word[i]) marks[i] = 'correct';
    else left[word[i]] = (left[word[i]] ?? 0) + 1;
  }
  for (let i = 0; i < LENGTH; i++) {
    if (marks[i] === 'correct') continue;
    if (left[guess[i]]) {
      marks[i] = 'present';
      left[guess[i]]--;
    }
  }
  return marks;
}

const RANK: Record<Mark, number> = { absent: 0, present: 1, correct: 2 };

/** The best mark each letter has had, for the keyboard. */
export function keyMarks(guesses: string[], word: string): Record<string, Mark> {
  const out: Record<string, Mark> = {};
  for (const g of guesses) {
    mark(g, word).forEach((m, i) => {
      const prev = out[g[i]];
      if (!prev || RANK[m] > RANK[prev]) out[g[i]] = m;
    });
  }
  return out;
}

export type Stats = { played: number; won: number; current: number; best: number; spread: number[] };

/** One person's stats from their games. The current streak runs back from today, or from yesterday while today's isn't finished. */
export function statsOf(games: Pick<Game, 'day' | 'guesses' | 'status'>[], now = today()): Stats {
  const done = games.filter((g) => g.status !== 'playing');
  const spread = Array(MAX_GUESSES).fill(0);
  for (const g of done) if (g.status === 'won') spread[guessesOf(g).length - 1]++;
  const byDay = new Map(done.map((g) => [g.day, g.status]));
  let best = 0;
  let run = 0;
  let prev: string | null = null;
  for (const day of [...byDay.keys()].sort()) {
    const won = byDay.get(day) === 'won';
    run = won ? (prev !== null && yesterday(day) === prev && run > 0 ? run + 1 : 1) : 0;
    best = Math.max(best, run);
    prev = day;
  }
  let current = 0;
  for (let day = byDay.has(now) ? now : yesterday(now); byDay.get(day) === 'won'; day = yesterday(day)) current++;
  return { played: done.length, won: done.filter((g) => g.status === 'won').length, current, best, spread };
}
