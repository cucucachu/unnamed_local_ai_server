# M2-07 — GATE G1+G2: browser → agent → real file write

**Milestone**: M2 · **Size**: M · **Depends on**: M1-02, M1-03, M2-06 · **Blocks**: M3-06

## Context

First end-to-end value: a user on a browser chats with the local model through the deep agent,
and the agent modifies a real file on disk. This gate combines PLAN.md's G1 (model serving) and
G2 (agentic chat) checks into one scripted pass + one human pass.

**Precondition**: `docs/TOOL_CALLING.md` verdict is GO or GO-WITH-FLAGS (PM-signed). If NO-GO,
this ticket is blocked — escalate.

## Spec

Write **`scripts/e2e/gate_m2.sh`** (bash, `set -euo pipefail`) that from a clean state:

1. `docker compose up -d --build` (all services defined so far), waits for model-runner healthy
   (≤ 10 min) and `curl http://localhost/api/health` OK.
2. **Chat streams**: runs `scripts/ws_smoke.py` (from M2-04) against
   `ws://localhost/ws/chat/gate-m2` with "Reply with one short sentence." → asserts ≥ 1 token
   frame + `turn_end`.
3. **Agent writes a real file**: same thread, sends
   `Create a file named gate-m2.txt in the workspace root containing exactly the text GATE-OK.
   Use your file tools.` Then polls `/srv/homeai/workspace/gate-m2.txt` on the host (≤ 90 s)
   and asserts its content is `GATE-OK` (whitespace-stripped). Retries the prompt once on
   failure (LLM nondeterminism allowance; 2 strikes = red).
4. **Persistence of the file across restart**: `docker compose restart agent-server`, assert
   the file still exists (bind-mount proof, PLAN.md P3-3).
5. **Web build serves**: `curl http://localhost/` contains the Expo bundle script tag.
6. Cleans up `gate-m2.txt`, prints `GATE M2: PASS`.

Fix whatever the script surfaces (that's the point of the gate ticket); keep fixes in their
owning services, not in the script.

## Out of scope

Thread persistence across restart (Postgres lands in M3-01 — chat memory loss on restart is
expected at this gate); code exec; files UI.

## Acceptance criteria (Tier A)

- [ ] `scripts/e2e/gate_m2.sh` committed, runs green on the host, twice in a row.

## Tier B (append to docs/HOST-CHECKS.md under M2 — the PM runs these)

- [ ] Phone browser at `http://homeai.local`: send "create a file called from-my-phone.txt
      containing hi" → tool card appears in chat → verify on the host the file exists.
- [ ] Tokens visibly stream (not one blob at the end).
- [ ] **PM sign-off recorded** at the top of docs/HOST-CHECKS.md: `G1+G2 passed <date>`.
