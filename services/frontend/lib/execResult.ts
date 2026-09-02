/**
 * Pure parser for the `execute_code` tool's `result_preview` text — the
 * exact format `services/agent-server/app/agent/execute_code_tool.py`'s
 * `_format_result` produces:
 *
 *   exit_code: {N}{" (TIMED OUT)" if timed_out else ""}
 *   --- stdout ---
 *   {stdout}
 *   --- stderr ---
 *   {stderr}
 *
 * optionally followed by a trailing `\n[output truncated]` line. `stdout`/
 * `stderr` are literally the string `"(empty)"` when there was no output
 * (substituted server-side before formatting) — this parser treats that as
 * ordinary body text, not a parse failure.
 */

export interface ParsedExecResult {
  exitCode: number | null;
  timedOut: boolean;
  body: string;
}

const EXIT_CODE_LINE = /^exit_code: (-?\d+)( \(TIMED OUT\))?\r?\n/;

/**
 * Parses one `execute_code` `result_preview` string. Returns
 * `{ exitCode: null, timedOut: false, body: preview }` (body = the raw
 * input, unchanged) whenever `preview` doesn't start with a well-formed
 * `exit_code: N` first line — callers use `exitCode === null` to detect
 * "not a real exec result" and fall back to generic rendering.
 */
export function parseExecResult(preview: string): ParsedExecResult {
  const match = EXIT_CODE_LINE.exec(preview);
  if (!match) {
    return { exitCode: null, timedOut: false, body: preview };
  }

  const exitCode = Number.parseInt(match[1], 10);
  if (Number.isNaN(exitCode)) {
    return { exitCode: null, timedOut: false, body: preview };
  }

  return {
    exitCode,
    timedOut: match[2] !== undefined,
    body: preview.slice(match[0].length),
  };
}
