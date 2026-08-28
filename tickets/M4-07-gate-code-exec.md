# M4-07 — GATE G4: agent writes+runs a script on real files; isolation green

**Milestone**: M4 · **Size**: S · **Depends on**: M4-05, M4-06, M3-06 · **Blocks**: M6-03

## Context

The README.md "hands + toolbox" promise, end to end: the agent authors a script with its file
tools, executes it in the sandbox, and the results land in the user's real files — with the
isolation suite green on the same build.

## Spec

Write **`scripts/e2e/gate_m4.sh`**:

1. Stack up healthy (`--build`).
2. Seed the host workspace: `gate-m4/photos/` with 5 dummy files `img_001.txt … img_005.txt`
   (stand-ins; content = their index).
3. Via WS, one prompt (retry once on failure):
   `In gate-m4/photos there are files named img_XXX.txt. Write a Python script in gate-m4/
   that renames each to renamed_XXX.txt, run it with execute_code, and confirm the result.`
4. Assert on the host (≤ 180 s): `gate-m4/*.py` exists; all five `renamed_*.txt` exist;
   no `img_*.txt` remain.
5. Frame log (capture WS frames to a file): contains ≥ 1 `tool_start` with category `file`
   AND ≥ 1 with category `exec` — proving both capability classes were used.
6. `scripts/verify_isolation.sh` → green.
7. `gate_m2.sh` + `gate_m3.sh` → still green (regression).
8. Cleanup; print `GATE M4: PASS`.

## Out of scope

Media (M5); real photo EXIF work (dummy files are the point — determinism).

## Acceptance criteria (Tier A)

- [ ] `scripts/e2e/gate_m4.sh` green twice in a row on the host.

## Tier B (append to docs/HOST-CHECKS.md under M4 — PM runs)

- [ ] From a phone: ask the agent to batch-process something real in your workspace (e.g.
      "make thumbnails of the images in test-photos/ using ffmpeg or imagemagick") and verify
      results in the files screen.
- [ ] PM sign-off line: `G4 passed <date>`.
