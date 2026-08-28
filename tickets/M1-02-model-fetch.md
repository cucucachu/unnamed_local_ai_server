# M1-02 — Model fetch script + first real inference

**Milestone**: M1 · **Size**: S · **Depends on**: M1-01 · **Blocks**: M1-03, M1-04, M2-07

## Context

Download the pinned GGUF into the gitignored models dir and prove real GPU inference end to end.
Model per CONVENTIONS.md §4: `ggml-org/gemma-4-26B-A4B-it-GGUF`, default file
`gemma-4-26B-A4B-it-Q4_K_M.gguf` (~16.8 GB).

## Spec

1. **`services/model-runner/fetch-model.sh`** — bash, `set -euo pipefail`:
   - Args: `[quant]`, default `Q4_K_M`. Maps to filename `gemma-4-26B-A4B-it-<QUANT>.gguf`.
   - Uses `huggingface_hub` CLI via uvx (no global install):
     `uvx --from huggingface_hub hf download ggml-org/gemma-4-26B-A4B-it-GGUF gemma-4-26B-A4B-it-<QUANT>.gguf --local-dir services/model-runner/models/`
     (verify exact repo filename with `hf download --help` / the repo tree first; if the repo
     shards files, download all shards for the quant).
   - Idempotent: skips if the target file exists with nonzero size; `--force` flag re-downloads.
   - Prints the file size and reminds the user to set `MODEL_FILE` in `.env` if a non-default
     quant was fetched.
2. Document usage in `README.md` quickstart (one line).
3. Bring the stack up and prove inference (AC below).

## Out of scope

Multiple quants (M1-04 fetches its own); mmproj/multimodal files (text-only for v1 — do NOT
download the vision adapter).

## Acceptance criteria (Tier A)

- [ ] `./services/model-runner/fetch-model.sh` downloads the Q4_K_M file into
      `services/model-runner/models/` (gitignored; `git status` stays clean).
- [ ] Re-running the script skips the download (idempotence).
- [ ] `docker compose up -d model-runner` reaches `healthy`
      (`docker compose ps --format json` shows healthy within 10 min).
- [ ] Non-streaming completion works (no ports are published, so run it inside the container):

```bash
docker compose exec model-runner curl -s http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma-4-26b-a4b-it","messages":[{"role":"user","content":"Reply with exactly: PONG"}],"max_tokens":8}'
```

   Response JSON contains a non-empty `choices[0].message.content`.
- [ ] `docker compose logs model-runner` shows layers offloaded to Vulkan device (grep
      `Vulkan`), not CPU-only.
- [ ] Record in `docs/ARCHITECTURE.md` (create file if absent, section "Model"): file, quant,
      size on disk, load time, and the offload log line.

## Tier B

None.
