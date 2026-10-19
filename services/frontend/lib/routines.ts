import { apiFetch } from './api';
import { spacePath, type Space } from './platform';

/**
 * Routines (M17-02..06, `/api/routines` in
 * `services/agent-server/app/api/routines.py`): saved prompts the agent
 * runs on a schedule, as their owner, in one of their spaces. Schedule
 * times are local to the routine's IANA `timezone`.
 */

export type RunStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'timed_out'
  | 'missed'
  | 'waiting_approval'
  | 'expired';

export type Weekday = 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun';
export const WEEKDAYS: Weekday[] = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];

export type Schedule =
  | { kind: 'once'; at: string }
  | { kind: 'daily'; time: string }
  | { kind: 'weekdays'; time: string }
  | { kind: 'weekly'; days: Weekday[]; time: string }
  | { kind: 'monthly'; day: number; time: string }
  | { kind: 'yearly'; month: number; day: number; time: string };

export type ScheduleKind = Schedule['kind'];

export interface LastRun {
  id: string;
  status: RunStatus;
  finished_at: string | null;
  thread_id: string | null;
}

export interface UnavailableTool {
  name: string;
  /** The tool's label in Settings → Agent tools. */
  label: string;
  group: string;
}

export interface Routine {
  id: string;
  name: string;
  /** For a delivered routine, only the part the user may edit. */
  prompt: string;
  /** `/personal` or `/spaces/<slug>`. */
  space: string;
  schedule: Schedule;
  timezone: string;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
  last_run?: LastRun | null;
  /** Built in (M21-07): can't be deleted, renamed or moved. */
  delivered?: boolean;
  /** The admin has it off for everyone (M21-08); the user's `enabled` is kept. */
  disabled_by_admin?: boolean;
  /** The part of a delivered routine's prompt that's always included. */
  fixed_prompt?: string | null;
  prompt_is_default?: boolean;
  unavailable_tools?: UnavailableTool[];
  /** Fewest days apart its runs may be (M22-14, the Memory reviews): 7, 28 or 365. */
  min_period?: number | null;
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
    case 'yearly': {
      const leap = schedule.month === 2 && schedule.day === 29 ? ' (or Feb 28)' : '';
      return `Every year on ${MONTHS[schedule.month - 1]} ${schedule.day}${leap} at ${clockTime(schedule.time)}`;
    }
  }
}

/** About how many days apart its runs are at least, as the server's
 * `schedule.period_days`; null for a one-off. Weekly on more than one day
 * runs more often than once a week, so it counts as daily. */
export function periodDays(schedule: Schedule): number | null {
  switch (schedule.kind) {
    case 'once':
      return null;
    case 'weekly':
      return schedule.days.length === 1 ? 7 : 1;
    case 'monthly':
      return 28;
    case 'yearly':
      return 365;
    default:
      return 1;
  }
}

const KIND_PERIODS: Record<ScheduleKind, number> = { once: 0, daily: 1, weekdays: 1, weekly: 7, monthly: 28, yearly: 365 };

/** The schedule kinds a routine with `minPeriod` can use (all of them without one). */
export function allowedKinds(minPeriod: number | null | undefined): ScheduleKind[] {
  const kinds = Object.keys(KIND_PERIODS) as ScheduleKind[];
  return minPeriod ? kinds.filter((kind) => KIND_PERIODS[kind] >= minPeriod) : kinds;
}

const PERIOD_WORDS: Record<number, string> = { 7: 'a week', 28: 'a month', 365: 'a year' };

/** Why `schedule` runs too often for a routine with `minPeriod`, or null. */
export function minPeriodProblem(schedule: Schedule, minPeriod: number | null | undefined): string | null {
  if (!minPeriod) return null;
  const period = periodDays(schedule);
  if (period !== null && period >= minPeriod) return null;
  const every = PERIOD_WORDS[minPeriod] ?? `${minPeriod} days`;
  return `It covers ${every}, so it can't run more often than once ${every}.`;
}

/** When it next runs, in the routine's own timezone. */
export function nextRunLabel(
  routine: Pick<Routine, 'next_run_at' | 'timezone' | 'enabled' | 'disabled_by_admin'>,
): string {
  if (!routine.enabled || routine.disabled_by_admin) return 'Off';
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

// --- spaces --------------------------------------------------------------------

export { spacePath };

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

const STATUS_LABELS: Record<RunStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  succeeded: 'Finished',
  failed: 'Failed',
  timed_out: 'Timed out',
  missed: 'Missed',
  waiting_approval: 'Needs approval',
  expired: 'Approval expired',
};

/** "Search the web, Read web pages, Integrations": a group that contributes
 * more than two tools is named once instead of tool by tool. */
export function unavailableSummary(tools: UnavailableTool[]): string {
  const counts = new Map<string, number>();
  for (const tool of tools) counts.set(tool.group, (counts.get(tool.group) ?? 0) + 1);
  const names: string[] = [];
  for (const tool of tools) {
    const name = (counts.get(tool.group) ?? 0) > 2 ? tool.group : tool.label;
    if (!names.includes(name)) names.push(name);
  }
  return names.join(', ');
}

export function runStatusLabel(status: RunStatus): string {
  return STATUS_LABELS[status] ?? status;
}
