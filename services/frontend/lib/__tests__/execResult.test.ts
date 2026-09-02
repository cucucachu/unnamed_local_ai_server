import { parseExecResult, type ParsedExecResult } from '../execResult';

describe('parseExecResult', () => {
  const cases: { name: string; input: string; expected: ParsedExecResult }[] = [
    {
      name: 'success (exit_code: 0) with stdout only',
      input: 'exit_code: 0\n--- stdout ---\nhello\n\n--- stderr ---\n(empty)',
      expected: {
        exitCode: 0,
        timedOut: false,
        body: '--- stdout ---\nhello\n\n--- stderr ---\n(empty)',
      },
    },
    {
      name: 'nonzero exit code',
      input: 'exit_code: 1\n--- stdout ---\n(empty)\n--- stderr ---\nsomething broke',
      expected: {
        exitCode: 1,
        timedOut: false,
        body: '--- stdout ---\n(empty)\n--- stderr ---\nsomething broke',
      },
    },
    {
      name: 'timed out (exit_code: 124 with the "(TIMED OUT)" suffix)',
      input: 'exit_code: 124 (TIMED OUT)\n--- stdout ---\npartial output\n--- stderr ---\n(empty)',
      expected: {
        exitCode: 124,
        timedOut: true,
        body: '--- stdout ---\npartial output\n--- stderr ---\n(empty)',
      },
    },
    {
      name: 'truncated output keeps the trailing "[output truncated]" line in body',
      input: 'exit_code: 0\n--- stdout ---\nlots of output\n--- stderr ---\n(empty)\n[output truncated]',
      expected: {
        exitCode: 0,
        timedOut: false,
        body: '--- stdout ---\nlots of output\n--- stderr ---\n(empty)\n[output truncated]',
      },
    },
    {
      name: 'both stdout and stderr empty (literal "(empty)" is ordinary body text, not a parse failure)',
      input: 'exit_code: 0\n--- stdout ---\n(empty)\n--- stderr ---\n(empty)',
      expected: {
        exitCode: 0,
        timedOut: false,
        body: '--- stdout ---\n(empty)\n--- stderr ---\n(empty)',
      },
    },
    {
      name: 'a negative exit code still parses (defensive — not produced by this repo today)',
      input: 'exit_code: -1\n--- stdout ---\n(empty)\n--- stderr ---\n(empty)',
      expected: {
        exitCode: -1,
        timedOut: false,
        body: '--- stdout ---\n(empty)\n--- stderr ---\n(empty)',
      },
    },
    {
      name: 'malformed input (garbage string) falls back to exitCode: null, body = raw input',
      input: 'execute_code failed: 502 Bad Gateway',
      expected: { exitCode: null, timedOut: false, body: 'execute_code failed: 502 Bad Gateway' },
    },
    {
      name: 'empty string falls back to exitCode: null, body = ""',
      input: '',
      expected: { exitCode: null, timedOut: false, body: '' },
    },
  ];

  for (const { name, input, expected } of cases) {
    it(name, () => {
      expect(parseExecResult(input)).toEqual(expected);
    });
  }
});
