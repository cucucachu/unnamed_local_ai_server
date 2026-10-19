import type { IconName } from '@expo/vector-icons';

export type Kind = 'book' | 'film' | 'show' | 'game' | 'album' | 'podcast';
export type Status = 'want' | 'started' | 'finished' | 'dropped';
export type Item = {
  id: number;
  title: string;
  creator: string;
  kind: Kind;
  status: Status;
  rating: number | null;
  started_on: string | null;
  finished_on: string | null;
  notes: string;
};

export const ITEM_COLUMNS = 'id, title, creator, kind, status, rating, started_on, finished_on, notes';

export const KINDS: { value: Kind; label: string; icon: IconName; creator: string }[] = [
  { value: 'book', label: 'Book', icon: 'book-outline', creator: 'Author' },
  { value: 'film', label: 'Film', icon: 'film-outline', creator: 'Director' },
  { value: 'show', label: 'Show', icon: 'tv-outline', creator: 'Made by' },
  { value: 'game', label: 'Game', icon: 'game-controller-outline', creator: 'Studio' },
  { value: 'album', label: 'Album', icon: 'musical-notes-outline', creator: 'Artist' },
  { value: 'podcast', label: 'Podcast', icon: 'mic-outline', creator: 'Host' },
];

/** The statuses, for pickers; `statusLabel` words them per kind ("Reading", "Watching"). */
export const STATUSES: { value: Status; label: string }[] = [
  { value: 'want', label: 'Want to' },
  { value: 'started', label: 'On it now' },
  { value: 'finished', label: 'Finished' },
  { value: 'dropped', label: 'Gave up' },
];

export const kindOf = (k: Kind) => KINDS.find((x) => x.value === k) ?? KINDS[0];

const VERB: Record<Kind, { now: string; past: string; to: string }> = {
  book: { now: 'Reading', past: 'Read', to: 'read' },
  film: { now: 'Watching', past: 'Watched', to: 'watch' },
  show: { now: 'Watching', past: 'Watched', to: 'watch' },
  game: { now: 'Playing', past: 'Played', to: 'play' },
  album: { now: 'Listening', past: 'Listened', to: 'listen to' },
  podcast: { now: 'Listening', past: 'Listened', to: 'listen to' },
};

/** "Reading since Oct 2", "Watched Sep 30", "Want to read", "Gave up". */
export function statusLabel(i: Pick<Item, 'kind' | 'status' | 'started_on' | 'finished_on'>): string {
  const v = VERB[i.kind];
  if (i.status === 'want') return `Want to ${v.to}`;
  if (i.status === 'started') return i.started_on ? `${v.now} since ${short(i.started_on)}` : v.now;
  if (i.status === 'finished') return i.finished_on ? `${v.past} ${short(i.finished_on)}` : v.past;
  return i.finished_on ? `Gave up ${short(i.finished_on)}` : 'Gave up';
}

/** ★★★☆☆ */
export function stars(rating: number | null): string {
  return rating ? '★'.repeat(rating) + '☆'.repeat(5 - rating) : '';
}

const pad = (n: number) => String(n).padStart(2, '0');

/** Today's local date, YYYY-MM-DD. */
export function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** "Oct 2", with the year if it isn't this one. */
export function short(day: string): string {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, y === new Date().getFullYear() ? { month: 'short', day: 'numeric' } : { month: 'short', day: 'numeric', year: 'numeric' });
}
