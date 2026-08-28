# M4-06 — Exec status/output rendering in chat UI

**Milestone**: M4 · **Size**: S · **Depends on**: M4-04, M2-06 · **Blocks**: M4-07

## Context

`execute_code` tool frames already flow to the UI with `category: "exec"` (M2-04) and render as
generic tool cards (M2-06). Give them a purpose-built rendering: command visible, output as a
terminal block, exit status at a glance.

## Spec

1. In the tool-item card (M2-06's component), special-case `category === "exec"`:
   - Collapsed: terminal icon, first line of `args.command` (single line, ellipsized),
     spinner while running; on completion a green `✓ exit 0` or red `✗ exit N` / `⏱ timed out`
     chip — parse from the first line of `result_preview` (format fixed by M4-04:
     `exit_code: N...`).
   - Expanded (tap): full command in a monospace block, then the stdout/stderr sections of
     `result_preview` in a scrollable monospace block (max height ~40% of screen), preserving
     the `--- stdout ---` / `--- stderr ---` separators, `[output truncated]` note styled
     dim if present.
2. Extract the parser as `lib/execResult.ts` — `parseExecResult(preview: string):
   { exitCode: number|null, timedOut: boolean, body: string }` (null exitCode if preview
   doesn't match the format → fall back to generic rendering).
3. Non-exec tool cards unchanged.
4. **Tests** (jest): `parseExecResult` table-driven (success, nonzero, timed out, truncated,
   malformed → nulls); snapshot/shallow test of collapsed exec card in all three status
   states.

## Out of scope

Live streaming of stdout while a command runs (result arrives only at `tool_end` — v1
limitation, by design); ANSI color rendering; copy-to-clipboard.

## Acceptance criteria (Tier A)

- [ ] `npm test` + `npx tsc --noEmit` green; web export builds.
- [ ] Playwright addition to `chat_browser_smoke.sh`: prompt
      "Use execute_code to run: echo HELLO-UI" → an exec card appears, expanding it shows
      `HELLO-UI`, chip shows exit 0. Green on host.

## Tier B (append to docs/HOST-CHECKS.md under M4)

- [ ] Phone browser + Expo Go: exec card renders and expands cleanly at phone width.
