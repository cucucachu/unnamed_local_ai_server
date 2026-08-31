import { relativeTime } from '../relativeTime';

const NOW = new Date('2026-08-30T20:00:00.000Z').getTime();

describe('relativeTime', () => {
  const cases: { name: string; iso: string; expected: string }[] = [
    { name: 'a few seconds ago', iso: '2026-08-30T19:59:50.000Z', expected: 'just now' },
    { name: 'a timestamp slightly in the future (clock skew) still reads "just now"', iso: '2026-08-30T20:00:05.000Z', expected: 'just now' },
    { name: 'a few minutes ago', iso: '2026-08-30T19:55:00.000Z', expected: '5m ago' },
    { name: 'just under an hour ago rounds down to minutes', iso: '2026-08-30T19:01:00.000Z', expected: '59m ago' },
    { name: 'a couple hours ago', iso: '2026-08-30T18:00:00.000Z', expected: '2h ago' },
    { name: 'just under a day ago rounds down to hours', iso: '2026-08-29T21:00:00.000Z', expected: '23h ago' },
    { name: 'a couple days ago', iso: '2026-08-28T20:00:00.000Z', expected: '2d ago' },
    { name: 'just under a week ago rounds down to days', iso: '2026-08-24T21:00:00.000Z', expected: '5d ago' },
    { name: 'a couple weeks ago', iso: '2026-08-16T20:00:00.000Z', expected: '2w ago' },
    { name: 'an unparseable timestamp returns an empty string rather than "NaN ago"', iso: 'not-a-date', expected: '' },
  ];

  for (const { name, iso, expected } of cases) {
    it(name, () => {
      expect(relativeTime(iso, NOW)).toBe(expected);
    });
  }
});
