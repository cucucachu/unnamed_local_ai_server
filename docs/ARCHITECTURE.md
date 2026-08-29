# Architecture

This file is built up incrementally, ticket by ticket. For now it only covers
the model runner (M1-02); later tickets will add sections for the agent
server, exec sandboxing, storage, and networking.

## Model

- **HF repo**: [`ggml-org/gemma-4-26B-A4B-it-GGUF`](https://huggingface.co/ggml-org/gemma-4-26B-A4B-it-GGUF)
- **File / quant actually used**: `gemma-4-26B-A4B-it-Q4_0.gguf`
  - **Deviation from the original spec**: the Conventions & Contracts reference
    issue assumed a `Q4_K_M` quant (~16.8 GB). Verified against the live HF
    repo tree (`/api/models/ggml-org/gemma-4-26B-A4B-it-GGUF/tree/main`) on
    2026-08-29: no `Q4_K_M` file exists. This repo is auto-converted (per its
    own README: "automatically converted using https://github.com/ggml-org/convert")
    and only ships legacy quant types — `Q4_0` (~14.6 GB), `Q8_0` (~26.9 GB),
    `BF16` (~50.5 GB) — plus unrelated siblings (`mmproj-*` vision adapter,
    `dflash-*` speculative-decode draft model, `mtp-*` multi-token-prediction
    heads) that are out of scope for this text-only v1. `Q4_0`, the smallest
    real quant, was fetched instead and is now the script/`.env` default.
    See `services/model-runner/fetch-model.sh` for the full rationale.
- **Size on disk**: 14,618,145,824 bytes (13.62 GiB / 14.62 GB decimal),
  confirmed with `stat` against the file in `services/model-runner/models/`
  and matching the size reported by the HF API for that file exactly.
- **Model load time**: ~4.5 seconds from `load_model: loading model '...'`
  to `llama_server: model loaded` in the container logs. This is unusually
  fast because the 14 GB file was still fully resident in the host's page
  cache (91 GB RAM) right after the fetch script downloaded it — expect a
  load time closer to the time it takes to read ~14 GB off disk on a cold
  cache (e.g. after a host reboot).
- **GPU offload (Vulkan)**: confirmed full GPU offload, no CPU-only fallback.
  Key log lines (`docker compose logs model-runner`, requires
  `MODEL_EXTRA_ARGS` to include `--verbose` — see `.env.example` comment,
  llama-server's default log-verbosity threshold otherwise hides these):

  ```
  I cmn  common_param:   - Vulkan0 : AMD Radeon 890M Graphics (RADV STRIX1) (47275 MiB, 45557 MiB free)
  I llama_prepare_model_devices: using device Vulkan0 (AMD Radeon 890M Graphics (RADV STRIX1)) (0000:c1:00.0) - 45557 MiB free
  I load_tensors: offloading output layer to GPU
  I load_tensors: offloading 29 repeating layers to GPU
  I load_tensors: offloaded 31/31 layers to GPU
  I load_tensors:      Vulkan0 model buffer size = 13925.86 MiB
  ```

- **Sampling defaults**: `--temp 1.0 --top-p 0.95 --top-k 64` (per the model
  card, already in `docker-compose.yml`).
- **`MODEL_EXTRA_ARGS` additions**: `--verbose --reasoning-budget 0`.
  `--verbose` is required to see the Vulkan offload lines above (default
  verbosity threshold hides them). `--reasoning-budget 0` disables Gemma 4's
  default "auto" thinking mode — without it, short `max_tokens` completions
  (e.g. a `max_tokens: 8` smoke test) can spend the entire budget on hidden
  `<|channel>thought` content and return an empty `message.content`.
