# M6-03 — Full-scenario e2e script + workspace backup

**Milestone**: M6 · **Size**: M · **Depends on**: M4-07, M5-02, M6-01, M6-02 · **Blocks**: M6-04

## Context

The capstone: PLAN.md P5-3's full run-through as a repeatable script, plus the "don't lose the
workspace" fast-follow PLAN.md explicitly asks to consider (simple periodic backup).

## Spec

1. **`scripts/e2e/gate_full.sh`** — chains, in order, with a fresh `docker compose up -d
   --build`: `gate_m2.sh`, `persistence_smoke.sh`, `gate_m3.sh`, `exec_crossview_smoke.sh`,
   `gate_m4.sh`, `verify_isolation.sh`, `verify_network.sh`, `files_browser_smoke.sh`,
   `media_browser_smoke.sh`, `chat_browser_smoke.sh`. Prints a summary table
   (script × pass/fail × seconds). Any red → exit 1. Refactor the sub-scripts as needed so they
   are idempotent and don't fight over thread names/files (prefix all artifacts with the
   script name — do a sweep).
2. **Backup**: `infra/host/backup-workspace.sh`:
   - `rsync -a --delete $WORKSPACE_DIR/ $BACKUP_DIR/workspace/` where `BACKUP_DIR` comes from
     `.env` (new var, default `/srv/homeai/backups`; add to `.env.example` + CONVENTIONS is NOT
     updated by agents — note the addition in the PR for the PM to merge into CONVENTIONS).
   - Also dump Postgres: `docker compose exec -T postgres pg_dump -U $POSTGRES_USER
     $POSTGRES_DB | gzip > $BACKUP_DIR/pg/homeai-$(date +%F).sql.gz`, keep last 14 by count.
   - Idempotent, safe when stack is down (skip pg dump with a warning).
   - `install-backup-timer.sh`: installs a systemd timer (unit files in `infra/host/systemd/`)
     running the backup daily at 03:00; `--uninstall` flag.
3. **README**: "Backups" section — what's covered (workspace + DB), what's not (model files —
   re-downloadable), how to restore (rsync back + `gunzip | psql`, exact commands).

## Out of scope

Off-site/cloud backup; encryption; incremental snapshots (rsync full-mirror is the v1 answer).

## Acceptance criteria (Tier A)

- [ ] `scripts/e2e/gate_full.sh` green end to end on the host, twice in a row.
- [ ] `backup-workspace.sh` run: backup dir contains a workspace mirror (spot-check one file
      `cmp`) and a pg dump; restore drill: restore the pg dump into a scratch db
      (`createdb homeai_restore` in the container) without error.
- [ ] systemd timer installed and listed (`systemctl list-timers | grep homeai`); uninstall
      works; leave it **installed**.

## Tier B (append to docs/HOST-CHECKS.md under M6 — PM runs, final product acceptance)

- [ ] From a phone, run README.md's "What using it looks like" list end to end: organize a
      messy folder via chat; batch-rename via script; summarize a text file to a new file;
      play the result media; download a file. Each works from the couch.
- [ ] PM sign-off line: `v1 accepted <date>`.
