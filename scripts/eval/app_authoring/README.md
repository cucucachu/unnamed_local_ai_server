# App authoring eval (M13-03)

Risk gate for `docs/PLATFORM.md` §11: can the local model build working
apps? Ten prompts run end-to-end against the **real** model. A case passes
when the agent produces an app that **builds** (the platform build includes
the smoke render) **and** a scripted check of the data model succeeds
(expected columns exist; insert a row and read it back).

Bar: **≥ 7/10**. Below 5/10: stop and escalate, do not claim the gate
passed.

**Customize cases (M22-19).** Six more cases (`"kind": "customize"` in
`cases.json`) install a template from the catalog and then ask for a
change the way the runner's "Ask the agent" panel does: a thread titled
`Ask: <app>` whose first message is the app context (instance, space,
`app.json`, `AGENT.md`, `schema.sql`; `seedUserMessage` in
`services/frontend/lib/appAgent.ts`) followed by the prompt. Two each of:

- `field`: a new column. Pass when the column is in the instance's
  database and a source file uses it.
- `screen`: a new route file under `app/`.
- `chart`: a source file uses a `@homeai/ui` chart (the templates chosen
  have none).

All of them also need the turn's last `build_app` to have succeeded. They
are reported separately (`summary.customize`); the 7/10 bar is for the ten
create cases.

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
pass rate, failure taxonomy, which template `create_app` used, whether
the generated source called `useDatabase` and/or `useSQLiteContext`, and
the customize cases' results.
