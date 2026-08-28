# M1-04 — Quant benchmark, pick default

**Milestone**: M1 · **Size**: S · **Depends on**: M1-02 · **Blocks**: nothing (informative)

## Context

Pick the default quant with data instead of vibes: prompt-processing (pp) and token-generation
(tg) speed across quants on this exact GPU. PLAN.md P1-4. Non-blocking for other lanes.

## Spec

1. Fetch `Q5_K_M` and `Q6_K` variants with `fetch-model.sh <quant>` (keep Q4_K_M).
2. Benchmark each with `llama-bench` from inside the model-runner image (one-off
   `docker compose run --rm --entrypoint /app/llama-bench model-runner ...` — adjust binary
   path to the image layout), params: `-p 512 -n 128`, GPU offload full (`-ngl 999`), 3 reps.
   Stop the serving container first (VRAM contention).
3. Record in `docs/ARCHITECTURE.md` section "Quant benchmark": table (quant × file size ×
   pp t/s × tg t/s), the chosen default, and one sentence of rationale (rule: fastest tg whose
   quality tier is ≥ Q4_K_M; break ties toward smaller file).
4. If the winner isn't Q4_K_M, update `MODEL_FILE` default in `.env.example` and
   CONVENTIONS.md §3/§4.

## Out of scope

Quality/perplexity evaluation; context-length scaling tests; KV-cache quant experiments.

## Acceptance criteria (Tier A)

- [ ] `docs/ARCHITECTURE.md` contains the benchmark table with real numbers for ≥ 3 quants.
- [ ] `.env.example` `MODEL_FILE` matches the documented recommendation.
- [ ] Serving container restarted and healthy afterwards (`docker compose ps`).

## Tier B

None.
