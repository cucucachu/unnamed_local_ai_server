import { formToInput, initialForm, type RoutineFormState } from '@/components/RoutineForm';
import type { Space } from '@/lib/platform';
import {
  clockTime,
  nextRunLabel,
  ordinal,
  routineSpaces,
  scheduleSummary,
  spaceLabel,
  type Routine,
} from '@/lib/routines';

describe('scheduleSummary', () => {
  it.each([
    [{ kind: 'weekdays', time: '07:00:00' }, 'Weekdays at 7:00'],
    [{ kind: 'daily', time: '08:30' }, 'Every day at 8:30'],
    [{ kind: 'weekly', days: ['thu', 'mon'], time: '18:05:00' }, 'Mon, Thu at 18:05'],
    [{ kind: 'weekly', days: ['fri'], time: '09:00' }, 'Fridays at 9:00'],
    [{ kind: 'weekly', days: ['sat', 'sun'], time: '10:00' }, 'Weekends at 10:00'],
    [{ kind: 'weekly', days: ['mon', 'tue', 'wed', 'thu', 'fri'], time: '07:00' }, 'Weekdays at 7:00'],
    [{ kind: 'weekly', days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'], time: '07:00' }, 'Every day at 7:00'],
    [{ kind: 'monthly', day: 1, time: '09:00:00' }, 'Monthly on the 1st at 9:00'],
    [{ kind: 'monthly', day: 31, time: '09:00' }, 'Monthly on the 31st (or the last day) at 9:00'],
    [{ kind: 'once', at: '2026-11-02T08:30:00' }, 'Once on Nov 2, 2026 at 8:30'],
  ] as const)('%j reads "%s"', (schedule, words) => {
    expect(scheduleSummary(schedule as never)).toBe(words);
  });

  it('formats times and ordinals', () => {
    expect(clockTime('00:05:00')).toBe('0:05');
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 23].map(ordinal)).toEqual([
      '1st', '2nd', '3rd', '4th', '11th', '12th', '13th', '21st', '22nd', '23rd',
    ]);
  });
});

describe('nextRunLabel', () => {
  it('shows the next run in the routine’s timezone', () => {
    expect(
      nextRunLabel({ enabled: true, next_run_at: '2026-10-06T15:30:00Z', timezone: 'America/Los_Angeles' }),
    ).toBe('Tue, Oct 6, 8:30 AM');
  });

  it('says when a routine is off or has nothing coming up', () => {
    expect(nextRunLabel({ enabled: false, next_run_at: null, timezone: 'UTC' })).toBe('Off');
    expect(nextRunLabel({ enabled: true, next_run_at: null, timezone: 'UTC' })).toBe('Not scheduled');
  });
});

const space = (id: string, kind: Space['kind'], role: Space['role'], archived_at: string | null = null): Space => ({
  id,
  slug: id,
  name: id === 'me' ? 'Alice' : `Space ${id}`,
  kind,
  gid: 1,
  owner_user_id: null,
  role,
  created_at: '',
  archived_at,
});

describe('routine spaces', () => {
  it('offers personal first, then shared spaces the user can edit', () => {
    const spaces = [
      space('fam', 'shared', 'editor'),
      space('me', 'personal', 'owner'),
      space('view', 'shared', 'viewer'),
      space('old', 'shared', 'owner', '2026-01-01T00:00:00Z'),
    ];
    expect(routineSpaces(spaces).map((s) => s.id)).toEqual(['me', 'fam']);
    expect(spaceLabel('/personal', spaces)).toBe('Personal');
    expect(spaceLabel('/spaces/fam', spaces)).toBe('Space fam');
    expect(spaceLabel('/spaces/gone', spaces)).toBe('gone');
  });
});

const ROUTINE: Routine = {
  id: 'r1',
  name: 'Brief',
  prompt: 'Summarize.',
  space: '/spaces/fam',
  schedule: { kind: 'weekly', days: ['mon', 'thu'], time: '08:30:00' },
  timezone: 'Europe/Berlin',
  enabled: false,
  approval_mode: 'read_only',
  next_run_at: null,
  last_run_at: null,
  created_at: '',
  updated_at: '',
};

describe('routine form', () => {
  it('round-trips a routine', () => {
    const form = initialForm(ROUTINE);
    expect(form).toMatchObject({ kind: 'weekly', time: '08:30', days: ['mon', 'thu'], approvalMode: 'read_only' });
    expect(formToInput(form)).toEqual({
      input: {
        name: 'Brief',
        prompt: 'Summarize.',
        space: '/spaces/fam',
        schedule: { kind: 'weekly', days: ['mon', 'thu'], time: '08:30' },
        timezone: 'Europe/Berlin',
        enabled: false,
        approval_mode: 'read_only',
      },
    });
    const once = initialForm({ ...ROUTINE, schedule: { kind: 'once', at: '2026-11-02T07:05:00' } });
    expect([once.date, once.time]).toEqual(['2026-11-02', '07:05']);
  });

  it('defaults a new routine to the device timezone, weekdays, asking', () => {
    const form = initialForm(null);
    expect(form).toMatchObject({ kind: 'weekdays', space: '/personal', approvalMode: 'ask', enabled: true });
    expect(form.timezone).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
  });

  it.each([
    [{ name: ' ' }, 'Give the routine a name.'],
    [{ prompt: '' }, 'Say what the agent should do.'],
    [{ time: '25:00' }, 'Enter the time as HH:MM, e.g. 07:30.'],
    [{ kind: 'once', date: '11/02/2026' }, 'Enter the date as YYYY-MM-DD.'],
    [{ kind: 'weekly', days: [] }, 'Pick at least one day.'],
    [{ kind: 'monthly', monthDay: '32' }, 'Pick a day of the month from 1 to 31.'],
    [{ timezone: '' }, 'Enter a timezone, e.g. America/Los_Angeles.'],
  ] as [Partial<RoutineFormState>, string][])('rejects %j', (overrides, error) => {
    expect(formToInput({ ...initialForm(ROUTINE), ...overrides })).toEqual({ error });
  });

  it('pads a short hour', () => {
    const checked = formToInput({ ...initialForm(ROUTINE), kind: 'daily', time: '7:05' });
    expect('input' in checked && checked.input.schedule).toEqual({ kind: 'daily', time: '07:05' });
  });
});
