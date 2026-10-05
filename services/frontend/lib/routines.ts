import { apiFetch } from './api';
import type { RunStatus } from './inbox';
import type { Space } from './platform';

/**
 * Routines (M17-02..06, `/api/routines` in
 * `services/agent-server/app/api/routines.py`): saved prompts the agent
 * runs on a schedule, as their owner, in one of their spaces. Schedule
 * times are local to the routine's IANA `timezone`.
 */

export type Weekday = 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun';
export const WEEKDAYS: Weekday[] = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];

export type Schedule =
  | { kind: 'once'; at: string }
  | { kind: 'daily'; time: string }
  | { kind: 'weekdays'; time: string }
  | { kind: 'weekly'; days: Weekday[]; time: string }
  | { kind: 'monthly'; day: number; time: string };

export type ScheduleKind = Schedule['kind'];
export type ApprovalMode = 'ask' | 'allow_writes' | 'read_only';

export interface LastRun {
  id: string;
  status: RunStatus;
  finished_at: string | null;
  thread_id: string | null;
}

export interface Routine {
  id: string;
  name: string;
  prompt: string;
  /** `/personal` or `/spaces/<slug>`. */
  space: string;
  schedule: Schedule;
  timezone: string;
  enabled: boolean;
  approval_mode: ApprovalMode;
  next_run_at: string | null;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
  last_run?: LastRun | null;
}

export interface RoutineRun {
  id: string;
  trigger: 'schedule' | 'manual';
  status: RunStatus;
  detail: string | null;
  thread_id: string | null;
  due_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface RoutineInput {
  name: string;
  prompt: string;
  space: string;
  schedule: Schedule;
  timezone: string;
  enabled: boolean;
  approval_mode: ApprovalMode;
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

function path(id: string, rest = ''): string {
  return `/api/routines/${encodeURIComponent(id)}${rest}`;
}

export function listRoutines(): Promise<Routine[]> {
  return apiFetch<Routine[]>('/api/routines');
}

export function getRoutine(id: string): Promise<Routine> {
  return apiFetch<Routine>(path(id));
}

export function createRoutine(input: RoutineInput): Promise<Routine> {
  return apiFetch<Routine>('/api/routines', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(input) });
}

export function updateRoutine(id: string, changes: Partial<RoutineInput>): Promise<Routine> {
  return apiFetch<Routine>(path(id), { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(changes) });
}

export async function deleteRoutine(id: string): Promise<void> {
  await apiFetch<void>(path(id), { method: 'DELETE' });
}

export function runRoutineNow(id: string): Promise<RoutineRun> {
  return apiFetch<RoutineRun>(path(id, '/run'), { method: 'POST' });
}

export function listRoutineRuns(id: string): Promise<RoutineRun[]> {
  return apiFetch<RoutineRun[]>(path(id, '/runs'));
}

// --- words -----------------------------------------------------------------------

const DAY_NAMES: Record<Weekday, string> = {
  mon: 'Mon',
  tue: 'Tue',
  wed: 'Wed',
  thu: 'Thu',
  fri: 'Fri',
  sat: 'Sat',
  sun: 'Sun',
};
const DAY_PLURALS: Record<Weekday, string> = {
  mon: 'Mondays',
  tue: 'Tuesdays',
  wed: 'Wednesdays',
  thu: 'Thursdays',
  fri: 'Fridays',
  sat: 'Saturdays',
  sun: 'Sundays',
};
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** `"08:30"` or `"08:30:00"` as `"8:30"`. */
export function clockTime(time: string): string {
  const [hours, minutes = '00'] = time.split(':');
  return `${Number(hours)}:${minutes.padStart(2, '0')}`;
}

export function ordinal(n: number): string {
  const teen = n % 100 >= 11 && n % 100 <= 13;
  const suffix = teen ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th';
  return `${n}${suffix}`;
}

function sameDays(days: Weekday[], expected: Weekday[]): boolean {
  return days.length === expected.length && expected.every((day) => days.includes(day));
}

/** The schedule in words: "Weekdays at 7:00", "Mon, Thu at 8:30", ... */
export function scheduleSummary(schedule: Schedule): string {
  switch (schedule.kind) {
    case 'once': {
      const [date, time = '00:00'] = schedule.at.split('T');
      const [year, month, day] = date.split('-').map(Number);
      return `Once on ${MONTHS[month - 1]} ${day}, ${year} at ${clockTime(time)}`;
    }
    case 'daily':
      return `Every day at ${clockTime(schedule.time)}`;
    case 'weekdays':
      return `Weekdays at ${clockTime(schedule.time)}`;
    case 'weekly': {
      const days = WEEKDAYS.filter((day) => schedule.days.includes(day));
      const at = clockTime(schedule.time);
      if (days.length === 7) return `Every day at ${at}`;
      if (sameDays(days, ['mon', 'tue', 'wed', 'thu', 'fri'])) return `Weekdays at ${at}`;
      if (sameDays(days, ['sat', 'sun'])) return `Weekends at ${at}`;
      if (days.length === 1) return `${DAY_PLURALS[days[0]]} at ${at}`;
      return `${days.map((day) => DAY_NAMES[day]).join(', ')} at ${at}`;
    }
    case 'monthly': {
      const lastDay = schedule.day > 28 ? ' (or the last day)' : '';
      return `Monthly on the ${ordinal(schedule.day)}${lastDay} at ${clockTime(schedule.time)}`;
    }
  }
}

/** When it next runs, in the routine's own timezone. */
export function nextRunLabel(routine: Pick<Routine, 'next_run_at' | 'timezone' | 'enabled'>): string {
  if (!routine.enabled) return 'Off';
  if (!routine.next_run_at) return 'Not scheduled';
  try {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: routine.timezone,
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    }).format(new Date(routine.next_run_at));
  } catch {
    return new Date(routine.next_run_at).toISOString();
  }
}

export const APPROVAL_MODE_LABELS: Record<ApprovalMode, string> = {
  ask: 'Ask me',
  allow_writes: 'Allow writes',
  read_only: 'Read-only',
};

export const APPROVAL_MODE_HELP: Record<ApprovalMode, string> = {
  ask: 'Pauses at anything that changes files or data, until you approve it in its chat.',
  allow_writes: 'Writes in its space go ahead; deleting still asks.',
  read_only: "Can read and report, but can't change anything.",
};

// --- spaces --------------------------------------------------------------------

export function spacePath(space: Space): string {
  return space.kind === 'personal' ? '/personal' : `/spaces/${space.slug}`;
}

/** Spaces a routine may run in: the caller edits them and they're not archived. */
export function routineSpaces(spaces: Space[]): Space[] {
  const editable = spaces.filter((s) => (s.role === 'owner' || s.role === 'editor') && !s.archived_at);
  return [...editable.filter((s) => s.kind === 'personal'), ...editable.filter((s) => s.kind !== 'personal')];
}

export function spaceLabel(path: string, spaces: Space[]): string {
  if (path === '/personal') return 'Personal';
  const space = spaces.find((s) => spacePath(s) === path);
  return space?.name ?? path.replace(/^\/spaces\//, '');
}

export function deviceTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}
