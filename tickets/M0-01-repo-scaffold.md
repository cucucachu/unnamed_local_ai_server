# M0-01 — Repo scaffold, compose skeleton, `.env` contract

**Milestone**: M0 · **Size**: M · **Depends on**: — · **Blocks**: everything

## Context

First ticket in the repo. Establishes the directory layout from PLAN.md §"Repository layout",
the compose file that all service tickets extend, and the environment-variable contract from
[CONVENTIONS.md §3](./CONVENTIONS.md). No services are implemented here — only structure that
later tickets fill in.

## Spec

1. `git init` (branch `main`) if not already a repo.
2. Create this exact tree (empty dirs get a `.gitkeep`):

```
docker-compose.yml
.env.example
.gitignore
README.md
infra/
  caddy/            (filled by M0-03)
  host/             (filled by M0-02)
services/
  model-runner/models/.gitkeep
  code-exec-manager/
  agent-server/
  frontend/
scripts/e2e/
docs/
  HOST-CHECKS.md    (header only: one "## M0" .. "## M6" heading each)
tickets/            (already exists — leave as is)
```

3. `.gitignore`: `services/model-runner/models/*.gguf`, `.env`, `__pycache__/`, `.venv/`,
   `node_modules/`, `dist/`, `.expo/`, `*.pyc`, `.pytest_cache/`, `.ruff_cache/`.
4. `.env.example`: every variable from CONVENTIONS.md §3, with its default, one comment line
   each explaining what it does. `POSTGRES_PASSWORD` present but empty with a `# REQUIRED`
   comment. Comment block at top explaining `cp .env.example .env` and how to find
   `RENDER_GID`/`VIDEO_GID` (`getent group render | cut -d: -f3`).
5. `docker-compose.yml`:
   - `name: homeai`
   - `networks: { homeai-net: { driver: bridge } }`
   - No services yet, but include a top-of-file comment listing which ticket adds which
     service block (caddy→M0-03, model-runner→M1-01, agent-server→M2-01, postgres→M3-01,
     code-exec-manager→M4-03).
6. `README.md`: **already done** (converted from the old `PRODUCT.md` ahead of schedule) — verify
   it still has a product description, a quickstart section (clone → `cp .env.example .env` →
   run host scripts → `docker compose up -d`), and links to PLAN.md / `tickets/BACKLOG.md` /
   `docs/HOST-CHECKS.md`; update the quickstart section to reflect the real compose file once
   this ticket lands.

## Out of scope

Any service implementation; host scripts (M0-02); Caddyfile (M0-03).

## Acceptance criteria (Tier A)

- [ ] `git rev-parse --is-inside-work-tree` succeeds; initial commit exists containing the tree.
- [ ] `docker compose config -q` exits 0 with `.env` copied from `.env.example` (fill
      `POSTGRES_PASSWORD=dev` for the check).
- [ ] `.env.example` contains every variable named in CONVENTIONS.md §3 (grep each name).
- [ ] Tree matches the spec (verify with `ls`).

## Tier B

None.
