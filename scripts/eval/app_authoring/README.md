# App authoring eval (M13-03)

Risk gate for `docs/PLATFORM.md` §11: can the local model build working
apps? Ten prompts run end-to-end against the **real** model. A case passes
when the agent produces an app that **builds** (the platform build includes
the smoke render) **and** a scripted check of the data model succeeds
(expected columns exist; insert a row and read it back).

Bar: **≥ 7/10**. Below 5/10: stop and escalate, do not claim the gate
passed.

## Run

Needs the live stack (GPU model, platform, agent-server with the templates
and authoring guide, `homeai-app-builder:latest` with the SDK that exports
`useSQLiteContext`). Takes `/tmp/homeai-stack.lock` itself.

```bash
scripts/eval/app_authoring/run.sh
EVAL_ONLY=grocery-quantities scripts/eval/app_authoring/run.sh
EVAL_LIMIT=2 scripts/eval/app_authoring/run.sh
```

Creates a throwaway `e2e-authoring-*` user, turns HITL off, and deletes the
user / personal space / app bundles on exit. Never completes bootstrap.
Never recreates `model-runner` or `postgres`.

Report: `scripts/eval/app_authoring/last-report.json` (gitignored) with
pass rate, failure taxonomy, which template `create_app` used, and whether
the generated source called `useDatabase` and/or `useSQLiteContext`.
