# Routine eval (M17-07)

Can the local model turn natural-language requests into the right routine
schedules? Four prompts ("every weekday at 7am", "tomorrow at 9:30", "the
first of every month at 8pm", "Monday and Thursday at quarter past six in the
evening") each run in a fresh chat against the **real** model. A case passes
when the agent's `create_routine` approval card has the expected schedule and
the user's timezone (set to `America/Los_Angeles` first). Every card is
rejected, so no routine is saved.

Bar: all of them.

## Run

Needs the live stack with an agent-server that has the routine tools. Takes
`/tmp/homeai-stack.lock` itself.

```bash
scripts/eval/routines/run.sh
EVAL_ONLY=tomorrow scripts/eval/routines/run.sh
```

Creates a throwaway `e2e-routines-*` user and deletes it on exit. Report:
`scripts/eval/routines/last-report.json` (gitignored).
