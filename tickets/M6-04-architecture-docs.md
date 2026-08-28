# M6-04 — ARCHITECTURE.md final write-up

**Milestone**: M6 · **Size**: S · **Depends on**: M6-03 · **Blocks**: — (last ticket)

## Context

Consolidate what was actually built (not just planned) into `docs/ARCHITECTURE.md` so future
work (fast-follows, new tools) starts from truth. PLAN.md P5-4.

## Spec

Restructure/complete `docs/ARCHITECTURE.md` (it already holds the model + benchmark + isolation
sections from earlier tickets — keep them, organized):

1. **System overview** — copy the two mermaid diagrams from PLAN.md, correcting anything that
   drifted during implementation (diff against the real compose file and code; the doc must
   match reality, note deviations explicitly with a "changed because" line).
2. **Service catalog** — one subsection per service: purpose, image/base, ports, mounts, env
   consumed, where its tests live, how to run them.
3. **Contracts** — link to `tickets/CONVENTIONS.md` §5–§8 as the API source of truth (don't
   duplicate).
4. **Model operations** — how to swap quant/model (fetch script + `.env` + restart), benchmark
   results table, context-size tradeoffs, tool-calling verdict summary (link
   `docs/TOOL_CALLING.md`).
5. **Security model** — the isolation boundary as implemented (§7 spec), what
   `verify_isolation.sh` proves, threat-model one-pager (trusted LAN, untrusted model output,
   untrusted executed code), and the documented fast-follow hardening list from PLAN.md
   Phase 6 verbatim.
6. **Operations** — start/stop/update, logs, backup/restore, host checklist location, e2e gate
   scripts and when to run them.
7. Update root `README.md` to link every doc; verify every relative link in `docs/` resolves
   (`npx markdown-link-check` or a simple grep-based check script — either is fine).

## Out of scope

New features; rewriting PLAN.md (it stays as a historical intent doc — add a one-line banner
at the top of PLAN.md: "Superseded by docs/ARCHITECTURE.md for as-built details").

## Acceptance criteria (Tier A)

- [ ] `docs/ARCHITECTURE.md` contains all six sections with real (verified) values — service
      table cross-checked against `docker compose config`.
- [ ] Link check passes.
- [ ] PLAN.md banner added.

## Tier B

- [ ] PM reads it top to bottom; anything surprising becomes a correction commit or a new
      ticket.
