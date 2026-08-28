# M3-06 — GATE G3: restart persistence + files round-trip

**Milestone**: M3 · **Size**: S · **Depends on**: M3-02, M3-03, M3-04, M3-05, M2-07 · **Blocks**: M4-07

## Context

Proves the "remembers the state of your files across sessions" and "real file-management
partner" product promises (README.md): everything survives a full stack restart, and the
chat↔files loop closes in the UI.

## Spec

Write **`scripts/e2e/gate_m3.sh`**:

1. `docker compose up -d --build`; wait healthy.
2. Create a thread via REST; over WS ask the agent to create `reports/gate-m3.md` with content
   `persistent` (poll host path, retry-once policy as gate_m2).
3. `GET /api/files?path=reports` → entry present. Download → content matches.
4. **Full restart**: `docker compose down && docker compose up -d` (no `-v`!); wait healthy.
5. Thread still listed via REST; `GET messages` still returns the turn; the file still exists
   via files API **and** on the host path.
6. Continue the conversation on the same thread ("what file did you just create?") → response
   references gate-m3 (loose grep `gate-m3`; retry once).
7. Cleanup (delete thread + file), print `GATE M3: PASS`.

## Out of scope

Code exec, media.

## Acceptance criteria (Tier A)

- [ ] `scripts/e2e/gate_m3.sh` green twice in a row on the host.
- [ ] `gate_m2.sh` still green (no regression).

## Tier B (append to docs/HOST-CHECKS.md under M3 — PM runs)

- [ ] Phone: create a thread + file via chat, reboot the **whole host machine**, confirm thread
      history and file are intact and chat continues. (The one check scripts can't do.)
- [ ] PM sign-off line: `G3 passed <date>`.
