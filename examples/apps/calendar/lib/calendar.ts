// Dates are local YYYY-MM-DD strings and times local HH:MM, as stored.

export type Repeat = 'none' | 'daily' | 'weekly' | 'monthly' | 'yearly';

export type CalendarEvent = {
  id: number;
  title: string;
  location: string;
  notes: string;
  all_day: number;
  start_date: string;
  start_time: string | null;
  end_date: string;
  end_time: string | null;
  repeat: Repeat;
  repeat_every: number;
  repeat_days: string;
  repeat_until: string | null;
  /** Comma-separated user ids from event_people; null when nobody. */
  people: string | null;
  /** Comma-separated skipped dates from event_skips; null when none. */
  skips: string | null;
};

export const EVENT_SELECT = `SELECT e.id, e.title, e.location, e.notes, e.all_day, e.start_date, e.start_time, e.end_date, e.end_time,
  e.repeat, e.repeat_every, e.repeat_days, e.repeat_until,
  (SELECT group_concat(p.user_id) FROM event_people p WHERE p.event_id = e.id) AS people,
  (SELECT group_concat(s.date) FROM event_skips s WHERE s.event_id = e.id) AS skips
FROM events e`;

/** One appearance of an event on the calendar: an occurrence of a series, or a birthday. */
export type Occurrence = {
  key: string;
  title: string;
  allDay: boolean;
  /** This occurrence's first and last day. */
  startDate: string;
  endDate: string;
  startTime: string | null;
  endTime: string | null;
  event?: CalendarEvent;
  birthday?: { instance: string; id: number };
};

export type View = 'day' | 'week' | 'month';

export const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
export const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

// --- dates -------------------------------------------------------------------

function parts(day: string): [number, number, number] {
  const [y, m, d] = day.split('-').map(Number);
  return [y, m, d];
}

/** Days since 1970-01-01. */
export function dayNumber(day: string): number {
  const [y, m, d] = parts(day);
  return Date.UTC(y, m - 1, d) / 86_400_000;
}

export function fromDayNumber(n: number): string {
  return new Date(n * 86_400_000).toISOString().slice(0, 10);
}

export function addDays(day: string, n: number): string {
  return fromDayNumber(dayNumber(day) + n);
}

export function weekday(day: string): number {
  return (dayNumber(day) + 4) % 7; // 1970-01-01 was a Thursday
}

export function isLeap(y: number): boolean {
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
}

function daysInMonth(y: number, m: number): number {
  return [31, isLeap(y) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1];
}

function ymd(y: number, m: number, d: number): string {
  return `${String(y).padStart(4, '0')}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

export function today(): string {
  const d = new Date();
  return ymd(d.getFullYear(), d.getMonth() + 1, d.getDate());
}

/** The same day of the month `n` months on (clamped to the month's length). */
export function addMonths(day: string, n: number): string {
  const [y, m, d] = parts(day);
  const total = y * 12 + (m - 1) + n;
  const ny = Math.floor(total / 12);
  const nm = (total % 12) + 1;
  return ymd(ny, nm, Math.min(d, daysInMonth(ny, nm)));
}

export function startOfWeek(day: string): string {
  return addDays(day, -weekday(day));
}

export function startOfMonth(day: string): string {
  return `${day.slice(0, 8)}01`;
}

/** The dates a view shows around `day`: a day, a Sunday-first week, or the whole weeks covering a month. */
export function viewRange(view: View, day: string): { from: string; to: string } {
  if (view === 'day') return { from: day, to: day };
  if (view === 'week') {
    const from = startOfWeek(day);
    return { from, to: addDays(from, 6) };
  }
  const first = startOfMonth(day);
  const last = addDays(addMonths(first, 1), -1);
  return { from: startOfWeek(first), to: addDays(startOfWeek(last), 6) };
}

/** Where the previous/next arrow goes from `day`. */
export function step(view: View, day: string, dir: 1 | -1): string {
  if (view === 'day') return addDays(day, dir);
  if (view === 'week') return addDays(day, 7 * dir);
  return addMonths(startOfMonth(day), dir);
}

export function daysBetween(from: string, to: string): string[] {
  const out: string[] = [];
  for (let n = dayNumber(from); n <= dayNumber(to); n++) out.push(fromDayNumber(n));
  return out;
}

const shortMonth = (m: number) => MONTHS[m - 1].slice(0, 3);

/** The heading for a view: "October 2026", "Oct 18 – 24, 2026", "Monday, Oct 19, 2026". */
export function viewTitle(view: View, day: string): string {
  const [y, m, d] = parts(day);
  if (view === 'month') return `${MONTHS[m - 1]} ${y}`;
  if (view === 'day') return `${WEEKDAYS[weekday(day)]}, ${shortMonth(m)} ${d}, ${y}`;
  const { from, to } = viewRange('week', day);
  const [fy, fm, fd] = parts(from);
  const [ty, tm, td] = parts(to);
  if (fy !== ty) return `${shortMonth(fm)} ${fd}, ${fy} – ${shortMonth(tm)} ${td}, ${ty}`;
  if (fm !== tm) return `${shortMonth(fm)} ${fd} – ${shortMonth(tm)} ${td}, ${ty}`;
  return `${shortMonth(fm)} ${fd} – ${td}, ${ty}`;
}

/** "Mon, Oct 19, 2026". */
export function dateLabel(day: string): string {
  const [y, m, d] = parts(day);
  return `${WEEKDAYS[weekday(day)].slice(0, 3)}, ${shortMonth(m)} ${d}, ${y}`;
}

/** "9:00 AM", "12:30 PM". */
export function timeLabel(time: string): string {
  const [h, min] = time.split(':').map(Number);
  return `${h % 12 === 0 ? 12 : h % 12}:${String(min).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
}

export function hourLabel(h: number): string {
  return `${h % 12 === 0 ? 12 : h % 12} ${h < 12 ? 'AM' : 'PM'}`;
}

export function minutes(time: string): number {
  const [h, m] = time.split(':').map(Number);
  return h * 60 + m;
}

export function fromMinutes(total: number): string {
  const t = Math.max(0, Math.min(total, 23 * 60 + 59));
  return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`;
}

// --- repeats -----------------------------------------------------------------

export function repeatDays(e: Pick<CalendarEvent, 'repeat_days' | 'start_date'>): number[] {
  const days = e.repeat_days
    .split(',')
    .filter((s) => /^[0-6]$/.test(s.trim()))
    .map(Number);
  return days.length ? [...new Set(days)].sort() : [weekday(e.start_date)];
}

/** "Every week on Mon, Wed", "Every 2 months", "Every year until Dec 31, 2026"; '' when it doesn't repeat. */
export function repeatLabel(e: CalendarEvent): string {
  if (e.repeat === 'none') return '';
  const unit = { daily: 'day', weekly: 'week', monthly: 'month', yearly: 'year' }[e.repeat];
  let s = e.repeat_every === 1 ? `Every ${unit}` : `Every ${e.repeat_every} ${unit}s`;
  if (e.repeat === 'weekly') s += ` on ${repeatDays(e).map((d) => WEEKDAYS[d].slice(0, 3)).join(', ')}`;
  if (e.repeat_until) s += ` until ${dateLabel(e.repeat_until)}`;
  return s;
}

/** The start dates of an event's occurrences that overlap from..to, without skipped ones. */
export function occurrenceDates(e: CalendarEvent, from: string, to: string): string[] {
  const span = dayNumber(e.end_date) - dayNumber(e.start_date);
  const first = dayNumber(e.start_date);
  const lo = dayNumber(from) - span; // an occurrence starting this early still reaches `from`
  const hi = Math.min(dayNumber(to), e.repeat_until ? dayNumber(e.repeat_until) : Infinity);
  const skips = new Set((e.skips ?? '').split(',').filter(Boolean));
  const out: string[] = [];
  const add = (n: number) => {
    if (n >= first && n >= lo && n <= hi) {
      const day = fromDayNumber(n);
      if (!skips.has(day)) out.push(day);
    }
  };
  const every = Math.max(1, e.repeat_every);
  if (e.repeat === 'none') {
    add(first);
  } else if (e.repeat === 'daily') {
    for (let k = Math.max(0, Math.ceil((lo - first) / every)); first + k * every <= hi; k++) add(first + k * every);
  } else if (e.repeat === 'weekly') {
    const week0 = first - weekday(e.start_date);
    const days = repeatDays(e);
    for (let k = Math.max(0, Math.floor((lo - week0) / 7 / every)); week0 + k * every * 7 <= hi; k++) {
      for (const d of days) add(week0 + k * every * 7 + d);
    }
  } else {
    const [y, m, d] = parts(e.start_date);
    const months = e.repeat === 'monthly' ? every : every * 12;
    const [ly, lm] = parts(fromDayNumber(Math.max(lo, first)));
    let k = Math.max(0, Math.floor(((ly - y) * 12 + (lm - m) - 1) / months));
    for (; ; k++) {
      const total = y * 12 + (m - 1) + k * months;
      const oy = Math.floor(total / 12);
      const om = (total % 12) + 1;
      if (dayNumber(ymd(oy, om, 1)) > hi) break;
      if (d <= daysInMonth(oy, om)) add(dayNumber(ymd(oy, om, d))); // no Feb 30th or 31st: that month is skipped
    }
  }
  return out;
}

export function occurrencesOf(e: CalendarEvent, from: string, to: string): Occurrence[] {
  const span = dayNumber(e.end_date) - dayNumber(e.start_date);
  return occurrenceDates(e, from, to).map((day) => ({
    key: `e${e.id}-${day}`,
    title: e.title,
    allDay: e.all_day === 1,
    startDate: day,
    endDate: addDays(day, span),
    startTime: e.start_time,
    endTime: e.end_time,
    event: e,
  }));
}

/** A birthday (from Contacts) as an all-day occurrence in each year it falls in from..to. Feb 29 is Feb 28 in other years. */
export function birthdaysIn(p: { _instance: string; id: number; name: string; birthday: string }, from: string, to: string): Occurrence[] {
  const [, m, d] = parts(p.birthday);
  const out: Occurrence[] = [];
  for (let y = parts(from)[0]; y <= parts(to)[0]; y++) {
    const day = ymd(y, m, m === 2 && d === 29 && !isLeap(y) ? 28 : d);
    if (day >= from && day <= to) {
      out.push({ key: `b${p._instance}-${p.id}-${y}`, title: `${p.name}'s birthday`, allDay: true, startDate: day, endDate: day, startTime: null, endTime: null, birthday: { instance: p._instance, id: p.id } });
    }
  }
  return out;
}

/** Whether an occurrence is on `day` at all. */
export function onDay(o: Occurrence, day: string): boolean {
  return o.startDate <= day && o.endDate >= day;
}

/** All-day first, then by start, then by title. */
export function compareOccurrences(a: Occurrence, b: Occurrence): number {
  if (a.allDay !== b.allDay) return a.allDay ? -1 : 1;
  const as = `${a.startDate} ${a.startTime ?? ''}`;
  const bs = `${b.startDate} ${b.startTime ?? ''}`;
  return as < bs ? -1 : as > bs ? 1 : a.title.localeCompare(b.title);
}

// --- the time grid -----------------------------------------------------------

/** A timed occurrence's part on one day, in minutes from midnight, and its column among overlapping ones. */
export type Block = { o: Occurrence; top: number; bottom: number; column: number; columns: number };

/** The timed occurrences on `day`, cut to that day and laid out side by side where they overlap. */
export function dayBlocks(occurrences: Occurrence[], day: string): Block[] {
  const blocks: Block[] = occurrences
    .filter((o) => !o.allDay && onDay(o, day))
    .map((o) => {
      const top = o.startDate === day ? minutes(o.startTime!) : 0;
      const bottom = o.endDate === day ? minutes(o.endTime!) : 24 * 60;
      return { o, top, bottom: Math.max(bottom, top + 15), column: 0, columns: 1 };
    })
    .sort((a, b) => a.top - b.top || b.bottom - a.bottom);
  // Clusters of transitively overlapping blocks share their width between columns.
  let cluster: Block[] = [];
  let clusterEnd = -1;
  const close = () => {
    const n = Math.max(1, ...cluster.map((b) => b.column + 1));
    for (const b of cluster) b.columns = n;
    cluster = [];
  };
  for (const b of blocks) {
    if (b.top >= clusterEnd) close();
    const used = new Set(cluster.filter((c) => c.bottom > b.top).map((c) => c.column));
    let col = 0;
    while (used.has(col)) col++;
    b.column = col;
    cluster.push(b);
    clusterEnd = Math.max(clusterEnd, b.bottom);
  }
  close();
  return blocks;
}

/** The form's fields, as saveEvent's params. */
export type EventValues = {
  title: string;
  location: string;
  notes: string;
  allDay: boolean;
  startDate: string;
  startTime: string;
  endDate: string;
  endTime: string;
  repeat: Repeat;
  repeatEvery: number;
  repeatDays: number[];
  repeatUntil: string | null;
  people: string[];
};

export function valuesOf(e: CalendarEvent, occurrence?: string): EventValues {
  const span = dayNumber(e.end_date) - dayNumber(e.start_date);
  const start = occurrence ?? e.start_date;
  return {
    title: e.title,
    location: e.location,
    notes: e.notes,
    allDay: e.all_day === 1,
    startDate: start,
    startTime: e.start_time ?? '09:00',
    endDate: addDays(start, span),
    endTime: e.end_time ?? '10:00',
    repeat: occurrence ? 'none' : e.repeat,
    repeatEvery: e.repeat_every,
    repeatDays: e.repeat === 'weekly' ? repeatDays(e) : [],
    repeatUntil: occurrence ? null : e.repeat_until,
    people: (e.people ?? '').split(',').filter(Boolean),
  };
}

export function saveEventParams(id: number | null, v: EventValues, skip?: { eventId: number; date: string }) {
  return {
    id,
    title: v.title.trim(),
    location: v.location.trim(),
    notes: v.notes.trim(),
    all_day: v.allDay ? 1 : 0,
    start_date: v.startDate,
    start_time: v.allDay ? null : v.startTime,
    end_date: v.endDate,
    end_time: v.allDay ? null : v.endTime,
    repeat: v.repeat,
    repeat_every: v.repeat === 'none' ? 1 : Math.max(1, Math.round(v.repeatEvery)),
    repeat_days: v.repeat === 'weekly' ? [...v.repeatDays].sort().join(',') : '',
    repeat_until: v.repeat === 'none' ? null : v.repeatUntil,
    people: JSON.stringify(v.people),
    skip_event_id: skip?.eventId ?? null,
    skip_date: skip?.date ?? null,
  };
}

/** What's wrong with the form, or null. */
export function validate(v: EventValues): string | null {
  if (!v.title.trim()) return 'Give it a title.';
  if (!v.startDate || !v.endDate) return 'Pick the dates.';
  if (!v.allDay && (!v.startTime || !v.endTime)) return 'Pick the times.';
  const start = `${v.startDate} ${v.allDay ? '' : v.startTime}`;
  const end = `${v.endDate} ${v.allDay ? '' : v.endTime}`;
  if (end < start) return 'It has to end after it starts.';
  if (v.repeat !== 'none' && v.repeatUntil && v.repeatUntil < v.startDate) return 'The repeat has to end after the first one.';
  return null;
}
