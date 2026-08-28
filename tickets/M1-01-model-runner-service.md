# M1-01 — model-runner container (Vulkan llama.cpp)

**Milestone**: M1 · **Size**: M · **Depends on**: M0-01 · **Blocks**: M1-02

## Context

llama.cpp's `server-vulkan` image on the Radeon 890M (gfx1150) via `/dev/dri` passthrough.
Vulkan (RADV) is chosen over ROCm deliberately — see PLAN.md "Architecture decisions" (GTT
memory visibility, perf). Known issue: the upstream image misses GL/EGL loader libs
(llama.cpp GH issue #17761), fixed with an apt layer.

## Spec

1. **`services/model-runner/Dockerfile`**:

```dockerfile
FROM ghcr.io/ggml-org/llama.cpp:server-vulkan
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglvnd0 libgl1 libegl1 libgles2 \
    && rm -rf /var/lib/apt/lists/*
```

   After first successful build, resolve the base image digest
   (`docker inspect --format='{{index .RepoDigests 0}}' ghcr.io/ggml-org/llama.cpp:server-vulkan`)
   and pin `FROM` by digest, keeping the tag as a comment. Confirm the build date is post-Apr-2026
   (Gemma 4 chat-template fix) — check image labels/`llama-server --version`; if older, use tag
   `server-vulkan` latest and note the digest you pinned.

2. **compose service** (add to `docker-compose.yml`):

```yaml
model-runner:
  build: ./services/model-runner
  restart: unless-stopped
  networks: [homeai-net]
  devices:
    - /dev/dri:/dev/dri
  group_add:
    - "${RENDER_GID}"
    - "${VIDEO_GID}"
  ipc: host
  volumes:
    - ./services/model-runner/models:/models:ro
  command: >
    --model /models/${MODEL_FILE}
    --host 0.0.0.0 --port 8080
    --n-gpu-layers 999
    --jinja
    --ctx-size ${MODEL_CTX_SIZE}
    --temp 1.0 --top-p 0.95 --top-k 64
    ${MODEL_EXTRA_ARGS}
  healthcheck:
    test: ["CMD", "curl", "-sf", "http://localhost:8080/health"]
    interval: 15s
    timeout: 5s
    retries: 20
    start_period: 300s   # model load takes minutes
```

   Note: `/dev/kfd` (ROCm) is intentionally NOT passed — Vulkan needs only `/dev/dri`. If
   `--list-devices` (AC below) fails to find the GPU, add `/dev/kfd` and record why in a
   comment.

3. No model file exists yet (M1-02). For this ticket's AC, device detection is verified with a
   one-off run that doesn't need weights (see AC).

## Out of scope

Model download (M1-02); benchmarking (M1-04); any agent-server integration.

## Acceptance criteria (Tier A)

- [ ] `docker compose build model-runner` succeeds; `FROM` pinned by digest.
- [ ] Device visible:
      `docker compose run --rm --entrypoint /app/llama-server model-runner --list-devices`
      output contains a Vulkan device matching the Radeon 890M / RADV (`PHOENIX`/`GFX1150`/
      `Radeon` — assert on `Vulkan`+`Radeon`). (Adjust the binary path to the image's actual
      entrypoint if it differs; record it in a Dockerfile comment.)
- [ ] `docker compose config -q` passes; model-runner publishes no host ports.
- [ ] With a missing model file, `docker compose up model-runner` fails with a clear
      "model not found" error (sanity that command/env wiring is correct), not a Vulkan error.

## Tier B

None (GPU checks are automatable on-host).
