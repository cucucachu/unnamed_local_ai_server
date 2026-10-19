import { formToInput, initialForm, type RoutineFormState } from '@/components/RoutineForm';
import type { Space } from '@/lib/platform';
import {
  allowedKinds,
  clockTime,
  minPeriodProblem,
  nextRunLabel,
  periodDays,
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
  next_run_at: null,
  last_run_at: null,
  created_at: '',
  updated_at: '',
};

describe('routine form', () => {
  it('round-trips a routine', () => {
    const form = initialForm(ROUTINE);
    expect(form).toMatchObject({ kind: 'weekly', time: '08:30', days: ['mon', 'thu'] });
    expect(formToInput(form)).toEqual({
      input: {
        name: 'Brief',
        prompt: 'Summarize.',
        space: '/spaces/fam',
        schedule: { kind: 'weekly', days: ['mon', 'thu'], time: '08:30' },
        timezone: 'Europe/Berlin',
        enabled: false,
      },
    });
    const once = initialForm({ ...ROUTINE, schedule: { kind: 'once', at: '2026-11-02T07:05:00' } });
    expect([once.date, once.time]).toEqual(['2026-11-02', '07:05']);
  });

  it('defaults a new routine to the device timezone and weekdays', () => {
    const form = initialForm(null);
    expect(form).toMatchObject({ kind: 'weekdays', space: '/personal', enabled: true });
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

describe('yearly schedules and the reviews’ minimum period (M22-14)', () => {
  it('says a yearly schedule in words, Feb 29 falling back to the 28th', () => {
    expect(scheduleSummary({ kind: 'yearly', month: 1, day: 1, time: '08:07:00' })).toBe('Every year on Jan 1 at 8:07');
    expect(scheduleSummary({ kind: 'yearly', month: 2, day: 29, time: '08:00' })).toBe(
      'Every year on Feb 29 (or Feb 28) at 8:00',
    );
  });

  it('counts how often a schedule runs like the server', () => {
    expect(periodDays({ kind: 'daily', time: '08:00' })).toBe(1);
    expect(periodDays({ kind: 'weekly', days: ['mon'], time: '08:00' })).toBe(7);
    expect(periodDays({ kind: 'weekly', days: ['mon', 'thu'], time: '08:00' })).toBe(1);
    expect(periodDays({ kind: 'monthly', day: 1, time: '08:00' })).toBe(28);
    expect(periodDays({ kind: 'yearly', month: 1, day: 1, time: '08:00' })).toBe(365);
    expect(periodDays({ kind: 'once', at: '2026-11-02T08:30' })).toBeNull();
  });

  it('offers only the kinds a review can use, and says why one runs too often', () => {
    expect(allowedKinds(null)).toEqual(['once', 'daily', 'weekdays', 'weekly', 'monthly', 'yearly']);
    expect(allowedKinds(7)).toEqual(['weekly', 'monthly', 'yearly']);
    expect(allowedKinds(28)).toEqual(['monthly', 'yearly']);
    expect(allowedKinds(365)).toEqual(['yearly']);
    expect(minPeriodProblem({ kind: 'weekly', days: ['mon', 'thu'], time: '06:00' }, 7)).toBe(
      "It covers a week, so it can't run more often than once a week.",
    );
    expect(minPeriodProblem({ kind: 'weekly', days: ['sun'], time: '06:00' }, 7)).toBeNull();
    expect(minPeriodProblem({ kind: 'daily', time: '06:00' }, null)).toBeNull();
  });

  it('round-trips a yearly routine through the form', () => {
    const yearly = { kind: 'yearly', month: 12, day: 31, time: '20:00:00' } as const;
    const form = initialForm({ ...routineFor(yearly) });
    expect([form.kind, form.month, form.monthDay]).toEqual(['yearly', '12', '31']);
    const result = formToInput({ ...form, month: '13' });
    expect(result).toEqual({ error: 'Pick a month from 1 to 12.' });
    const ok = formToInput(form);
    expect('input' in ok && ok.input.schedule).toEqual({ kind: 'yearly', month: 12, day: 31, time: '20:00' });
  });
});

function routineFor(schedule: Routine['schedule']): Routine {
  return {
    id: 'r',
    name: 'New year',
    prompt: 'Plan the year.',
    space: '/personal',
    schedule,
    timezone: 'UTC',
    enabled: true,
    next_run_at: null,
    last_run_at: null,
    created_at: '',
    updated_at: '',
  };
}
