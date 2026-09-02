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

## Quant benchmark (M1-04)

Real `llama-bench` numbers on this exact host (AMD Radeon 890M iGPU, Ryzen AI
9 HX 370), used to pick the default quant instead of guessing. As noted in
`fetch-model.sh`, the model repo has no K-quants — the real choice is between
three legacy-type quants: `Q4_0` (noticeably lossy), `Q8_0` (near-lossless),
`BF16` (full precision).

### Benchmark mechanics

- **Binary**: the `server-vulkan` image (`ghcr.io/ggml-org/llama.cpp`) does
  **not** ship a standalone `/app/llama-bench` executable — only `/app/llama-server`
  and a unified dispatcher binary `/app/llama`, which exposes `bench` as a
  subcommand (`/app/llama help all` lists it alongside `serve`, `cli`,
  `quantize`, etc.). Confirmed via `docker run --rm --entrypoint /bin/sh ... -c
  "ls /app"` (no `llama-bench` file present) and `docker run --rm --entrypoint
  /app/llama ... help all`.
- **Invocation**: `docker compose run --rm --entrypoint /app/llama model-runner
  bench -m /models/<file> -p 512 -n 128 -ngl 999 -r 3 --verbose`. Passing
  `--entrypoint /app/llama` overrides the image's default entrypoint
  (`/app/llama-server`), and the `bench ...` arguments after the service name
  in `docker compose run` fully replace the compose file's `command:` block
  (which is llama-server-flavored and would otherwise be nonsensical for a
  bench run) — no compose file edits needed. The `-v/models:ro` volume mount
  and `group_add`/`devices` GPU-access config from the `model-runner` service
  definition still apply to `run` the same as `up`.
- **Serving container was stopped** (confirmed via `docker compose ps -a`
  showing no containers) before every benchmark run, per the ticket's
  GTT-contention warning.
- **Repetitions**: `-r 3` (3 repetitions per quant, per spec); `llama-bench`
  reports the mean ± stddev across those 3 reps directly.
- **Deviation — discarded a contaminated Q4_0 run**: an early sanity check
  (`-r 1`) was run in parallel with the still-in-progress `BF16` download and
  showed higher, misleadingly optimistic numbers (pp512 ≈ 411 t/s, tg128 ≈
  24.7 t/s) than the clean re-run after all downloads finished (pp512 ≈ 293,
  tg128 ≈ 17.8) — most likely explained by reduced memory-bandwidth
  contention once the download finished, on top of only 1 rep vs. 3. All
  numbers in the table below are from the **clean runs**, with no concurrent
  network/disk activity and no other containers running.

### Results

| Quant  | File size (decimal / GiB)     | pp512 (t/s)      | tg128 (t/s)     | Fully offloaded to Vulkan? |
| ------ | ------------------------------ | ---------------- | --------------- | --------------------------- |
| Q4_0   | 14.62 GB / 13.60 GiB           | 293.34 ± 1.77     | 17.80 ± 0.13    | Yes — `offloaded 31/31 layers to GPU`, Vulkan0 model buffer 13925.86 MiB |
| Q8_0   | 26.86 GB / 25.00 GiB           | 263.45 ± 1.94     | 12.62 ± 0.01    | Yes — `offloaded 31/31 layers to GPU`, Vulkan0 model buffer 25600.47 MiB |
| BF16   | 50.51 GB / 47.03 GiB           | 194.27 ± 1.93     | 6.68 ± 0.01     | Yes (after GTT cap raise, see below) — `offloaded 31/31 layers to GPU`, Vulkan0 model buffer 48150.36 MiB |

**Initial run: BF16 failed to load.** At the default GTT cap (Linux's `ttm`
allocator defaults to ~50% of system RAM — 45.67 GiB on this 91 GiB host),
BF16's weights alone need a `Vulkan0 model buffer size = 48150.36 MiB` (plus
`Vulkan_Host model buffer size = 1408.00 MiB`) — over budget before
KV-cache/compute buffers are even added. The real, literal failure:

```
load_tensors: offloaded 31/31 layers to GPU
load_tensors:      Vulkan0 model buffer size = 48150.36 MiB
load_tensors:  Vulkan_Host model buffer size =  1408.00 MiB
load_all_data: using async uploads for device Vulkan0, buffer type Vulkan0, backend Vulkan0
radv/amdgpu: Not enough memory for command submission.
ggml_vulkan: device lost on Vulkan0
llama_model_load: error loading model: vk::Queue::submit: ErrorDeviceLost
llama_bench: error: failed to load model '/models/gemma-4-26B-A4B-it-BF16.gguf'
```

This is a hard crash (Vulkan `ErrorDeviceLost`), not a graceful CPU fallback —
`llama-bench`'s `-ngl 999` forces every layer onto the GPU device rather than
auto-balancing across CPU/GPU. (A follow-up attempt at `-ngl 0`, immediately
after the crash, also failed with the same `ErrorDeviceLost` — the AMD/RADV
Vulkan device needs a brief recovery window after a device-lost event; a
later, unrelated Q4_0 run a few seconds after that confirmed the device had
recovered and worked normally again.)

**Follow-up: raised the GTT cap and re-benchmarked successfully.** The ~50%
default is a Linux kernel (`ttm` allocator) default, not a hardware limit —
on AMD APUs, "VRAM" is just system RAM the kernel is willing to map into the
GPU's address space, tunable via `ttm.pages_limit` / `ttm.page_pool_size` boot
params. Raised to 64 GiB (`ttm.pages_limit=16777216 ttm.page_pool_size=16777216`
in `GRUB_CMDLINE_LINUX_DEFAULT`, requires a reboot — this is a host-level
change, not something this repo's scripts manage, since it trades host RAM
for GPU-mappable RAM and the right value depends on what else runs on the
box). After rebooting and confirming `cat /sys/module/ttm/parameters/pages_limit`
read `16777216`, BF16 loaded and fully offloaded (31/31 layers) with the
same `docker compose run --rm --entrypoint /app/llama model-runner bench ...`
invocation, no other changes.

### Chosen default: `Q8_0`

**`Q8_0` is the new default** (`MODEL_FILE` in both `.env` and `.env.example`
updated from `gemma-4-26B-A4B-it-Q4_0.gguf` to `gemma-4-26B-A4B-it-Q8_0.gguf`).

Rationale: `Q8_0` fully offloads to the Vulkan GPU (same as `Q4_0`) and its
token-generation speed — **12.62 t/s** — is comfortably interactive (well
above typical reading speed) and only **~1.4x slower** than `Q4_0`'s 17.80
t/s (prompt-processing is even closer: 263 vs 293 t/s, ~1.1x). That modest
speed cost buys a near-lossless quant instead of `Q4_0`'s noticeably-lossy
legacy 4-bit quantization, and there is ample free memory (45+ GB GTT even
at the *default* cap) to afford the extra ~12 GB `Q8_0` needs on disk/GPU.

`BF16` remains excluded even though it *can* load after the GTT cap raise
(above): at **6.68 t/s** tg it's ~2.7x slower than `Q4_0` and ~1.9x slower
than `Q8_0` — noticeably less snappy for interactive chat — while costing
nearly 2x `Q8_0`'s disk/GPU footprint for a materially smaller quality gain
(BF16 vs. Q8_0 is a much smaller precision jump than Q8_0 vs. Q4_0). It also
requires a host-level GTT reconfiguration + reboot that most deployments
of this project won't want to make just to run the default model. `Q8_0`
remains the clear default; `BF16` is documented here as a working option for
anyone who's raised their GTT cap and wants maximum quality regardless of
speed.

### Quality: perplexity & KL-divergence vs. BF16 (follow-up)

The speed numbers above say nothing about *quality* — how much worse `Q8_0`
or `Q4_0` actually behave compared to full-precision `BF16`. Quantization
quality loss is a property of the quantized weights + eval text, not the
hardware running it, so this is measurable directly with `llama.cpp`'s
bundled `perplexity` subcommand (`/app/llama perplexity`), which supports
both plain perplexity (PPL) and `--kl-divergence` mode (compares a quant's
full output probability distribution, token-by-token, against a saved
full-precision reference — the same methodology the `llama.cpp` project
itself uses to publish quant-quality numbers).

**Methodology**: standard `wikitext-2-raw-v1` corpus (the community-standard
eval set for these comparisons). `BF16` was run first with
`--kl-divergence-base <file>` to save its per-token output distributions as
the reference; `Q8_0` and `Q4_0` were then each run with `--kl-divergence
--kl-divergence-base <file>` to compare against it. `ctx-size` 512 (default).

**Scaled down from the full corpus for a concrete, checked reason**: the
reference logits file turned out to store near-full-vocab log-probs per
scored token (~522 KB/token, measured directly from a 2-chunk test) — for
this model's large vocabulary, the *full* corpus (576 chunks / 294,912
tokens) would have produced a ~77 GB logits file. Scaled to **220 chunks**
(112,640 tokens, 56,320 scored) instead, producing a 29.41 GB file (matched
the pre-run prediction almost exactly) and ~27 min total runtime across all
3 models — comfortably bounded, while still a substantial, representative
sample (not a token effort).

**Results** — `Q8_0` vs `BF16` reference:

```
Mean KLD      : 0.691161 ± 0.008128
Median KLD    : 0.059910
Maximum KLD   : 32.827709
Mean Δp       : -0.073 ± 0.043 %
RMS Δp        : 10.146 ± 0.149 %
Same top p    : 76.800 ± 0.178 %
```

**Results** — `Q4_0` vs `BF16` reference:

```
Mean KLD      : 2.195803 ± 0.009057
Median KLD    : 1.667623
Maximum KLD   : 24.602465
Mean Δp       : -2.438 ± 0.092 %
RMS Δp        : 21.905 ± 0.145 %
Same top p    : 44.020 ± 0.210 %
```

**Interpretation**: `Q4_0` diverges from `BF16` roughly **3.2x more** than
`Q8_0` does (mean KLD 2.196 vs 0.691), and only picks the *same* most-likely
next token as `BF16` **44.0%** of the time, vs. `Q8_0`'s **76.8%** — nearly
double the agreement. Token-probability perturbation (RMS Δp) is also ~2.2x
larger for `Q4_0` (21.9% vs 10.1%). This quantitatively confirms the
qualitative call made above: `Q8_0` behaves much more like full-precision
`BF16` than `Q4_0` does, which is exactly the "near-lossless" property the
default-quant rationale relies on.

**Caveat — the raw PPL-ratio numbers from this tool are not trustworthy for
this comparison and were discarded.** The KL-run's *reconstructed* baseline
PPL (recovered from the 16-bit-quantized saved logits) didn't match `BF16`'s
own directly-measured PPL (7036 vs. 18412 — a 2.6x mismatch), and `Q4_0`'s
own live PPL (884) came out *lower* than `BF16`'s despite being the lossier
quant, which is nonsensical for a proper full-precision reference. Likely
cause: this is an *instruction-tuned* model being fed raw, unformatted
Wikipedia text with no chat template — exactly the out-of-distribution
scenario `llama.cpp`'s own perplexity docs warn can produce misleadingly
high/unstable perplexity, and the 16-bit log-prob storage used for
`--kl-divergence-base` doesn't seem to faithfully preserve the resulting
extreme-outlier surprisal values on reconstruction. KLD, Δp, and same-top-p
(reported above) are computed more robustly from the same run and don't
show this pathology, so those are the numbers to trust here — not the PPL
ratios.

## Isolation verification (M4-05)

Run `scripts/verify_isolation.sh` after any change to `code-exec-manager` or
the toolbox image (`services/code-exec-manager/exec-image/Dockerfile`) — it
is the scripted, repeatable check for the product's core safety promise
("safe to let it run code"), so it needs re-running whenever anything in
that promise's implementation moves.

It drives 17 checks against the live stack: 14 run commands **inside a real
exec container through the manager's own `POST /sessions/{id}/execute`
endpoint** (never via `docker exec` straight into the container, which would
bypass exactly what's being tested — an agent can only ever reach the
container through that same endpoint), plus 3 stack-level checks run
directly against the compose config and a `docker inspect` of that same
container. Together they cover every clause of
`services/code-exec-manager/app/sessions.py`'s `build_run_kwargs` (the exact
§7 hardening spec):

- **Network isolation** — no interfaces besides `lo`, no reachability to
  `agent-server`, the LAN, or the public internet, at both the shell
  (`curl`) and raw-socket (Python) level.
- **Filesystem isolation** — the root filesystem is read-only; `docker.sock`
  and agent-server's own `/app`/`/data` paths are absent; `/tmp` and
  `$HOME` are writable tmpfs; `/workspace` is the sole writable non-tmpfs
  (real bind) mount.
- **Capability dropping** — every Linux capability is dropped (`CapEff`
  all-zero), and the container runs as the configured non-root
  `HOMEAI_UID`, never root.
- **Resource limits** — the cgroup CPU quota and `memory.max` match
  `nano_cpus`/`mem_limit` exactly.
- **Secret non-leakage** — no environment variables reach the exec
  container at all (no `POSTGRES`, `MODEL_`, or similar secret-shaped
  names), matching `build_run_kwargs` never setting an `environment=` key.
- **Stack-level** — `code-exec-manager` remains the sole `docker.sock`
  holder (reuses M4-03's `scripts/check_socket_exclusivity.sh`), the exec
  container's own `docker inspect` confirms `NetworkMode`/`ReadonlyRootfs`/
  `CapDrop`/`Privileged`/bind-mount all match spec, and no compose service
  other than `caddy` publishes a host port.

Any failing check prints in red and the suite keeps running the rest (so a
single run shows every failure at once, not just the first), then exits 1
if anything failed. It's always safe to re-run: the exec container, its
manager session, and the throwaway "runner" container used to reach the
manager's REST API (see the script's own header comment for why a runner
container is needed at all — `code-exec-manager` publishes no host port) are
all cleaned up in an `EXIT` trap.
