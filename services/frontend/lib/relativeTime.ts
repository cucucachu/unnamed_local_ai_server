const MINUTE_MS = 60 * 1000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;
const WEEK_MS = 7 * DAY_MS;

/**
 * Tiny "X ago" formatter for thread-list rows — no date library per the
 * ticket ("implement a tiny helper, no date lib"). Deliberately coarse
 * (minutes/hours/days/weeks, no months/years — a self-hosted single-user
 * chat history realistically never needs those buckets, and adding them
 * would mean guessing at calendar-month semantics for no real benefit).
 *
 * `now` is injectable so tests don't depend on wall-clock time; defaults to
 * `Date.now()` for real callers.
 */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';

  const deltaMs = now - then;
  if (deltaMs < 0) return 'just now';
  if (deltaMs < MINUTE_MS) return 'just now';
  if (deltaMs < HOUR_MS) return `${Math.floor(deltaMs / MINUTE_MS)}m ago`;
  if (deltaMs < DAY_MS) return `${Math.floor(deltaMs / HOUR_MS)}h ago`;
  if (deltaMs < WEEK_MS) return `${Math.floor(deltaMs / DAY_MS)}d ago`;
  return `${Math.floor(deltaMs / WEEK_MS)}w ago`;
}
