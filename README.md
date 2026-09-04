# Home AI Agent

A private, local, always-on AI agent for your home network — a chat assistant with real file access and sandboxed code execution, plus a media-aware file browser, all served from one address to every device on your WiFi.

> **Status: planning / pre-implementation.** The architecture and full delivery backlog are written as [GitHub issues](../../issues); no services are built yet. See [Project status & roadmap](#project-status--roadmap).

## Table of contents

- [Overview](#overview)
- [Why](#why)
- [Guiding principles](#guiding-principles)
- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Project status & roadmap](#project-status--roadmap)
- [Working with the GitHub issues](#working-with-the-github-issues)
- [Getting started](#getting-started)
- [Backups](#backups)
- [License](#license)

## Overview

This repo builds a personal AI agent that lives on your own hardware and your own network. It's a single system, reachable from any device on your WiFi at one address, that combines:

- A private, local chat assistant — no cloud API, no internet dependency, no per-message cost.
- A real file-management partner that can create, edit, organize, rename, move, and clean up files for you, and remembers the state of your files across sessions because it works on the same persistent disk every time.
- The ability to actually run code to get things done (batch-processing files, writing small scripts, transforming data), contained so it can't damage the machine it runs on.
- A media-aware file browser so you can play back your videos and audio straight from the same interface, on your phone or your laptop.

In short: a private, always-on "computer-use" assistant for your home network, with hands (file access) and a sandboxed toolbox (code execution), instead of just a chat window.

**Who it's for**: you, on your home network. A single-user, trusted-LAN tool, not a multi-tenant product, not exposed to the internet.

**What using it looks like**:

- *"Organize my Downloads folder — group by type, get rid of obvious junk."*
- *"Rename these photos to their capture dates and put them in folders by month."*
- *"Summarize these PDFs and save the summary as a new file next to them."*
- *"Convert this batch of videos to a smaller format."*
- *"Pull up that video from last week's project on my phone."*
- *"Help me write and test a small script."*

## Why

- **Your data stays yours.** Files, conversations, and anything the agent touches never leave your network.
- **No usage limits, no subscription, no metering.** Once it's running, ten things or ten thousand things cost the same: electricity.
- **It can actually do things, not just describe them.** It edits your real filesystem directly and shows you the result in the same UI.
- **It's safe to let it run code.** Code execution happens in a locked-down, disposable container that can touch your files but nothing else on the machine.
- **It's available anywhere on your network.** Same address, same app, from your phone on the couch or your laptop at the desk — including media playback.
- **It's a foundation, not a toy.** Swappable model, swappable quant, a real API layer, a real sandbox boundary — built to grow instead of dead-ending as a weekend hack.

## Guiding principles

- **Local-first, always.** Inference, file storage, and code execution all happen on this machine. Internet access (if any) is incidental — for pulling container images or model files — never required day to day.
- **Give it real capability, then contain the risk.** Genuine, persistent file access, because that's the point of the tool. Anything riskier — arbitrary code execution — is isolated behind a hard boundary instead of being restricted into uselessness.
- **One address, every device.** `homeai.local` (or wherever it lands) is the whole interface, from any device on the network.
- **Minimum viable now, room to grow later.** Get the core loop (chat, files, code, media) working end to end, with a documented path to add polish and hardening later without rearchitecting.

## Architecture

Native Linux host + Docker Compose, five services behind one reverse
proxy: **caddy** (LAN entry point + static Expo web build), **agent-server**
(FastAPI + `deepagents`, direct persistent filesystem access via a
bind-mounted workspace), **model-runner** (`llama.cpp` Vulkan build serving
Gemma 4 on the iGPU), **code-exec-manager** (the sole `docker.sock` holder,
spinning up locked-down, session-scoped containers for the agent's
`execute_code` tool), and **postgres** (thread/checkpoint state). Code
execution is deliberately isolated from agent-server's own app code,
secrets, and every other service — see the isolation boundary described
below. The whole stack is LAN-only by design (no auth, no TLS, no internet
exposure) via network topology + host firewall, not application logic.

**Full as-built detail — the service catalog (ports/mounts/env), system
diagrams, model operations, security model, and day-to-day operations —
lives in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).** This section is
intentionally just a summary so the same content isn't maintained in two
places; that document is the source of truth for anything more detailed
than the paragraph above, including the "Documented fast-follows" list and
the system diagrams.

## Repository layout

Target tree (built incrementally by the milestones in [Project status & roadmap](#project-status--roadmap)):

```
unnamed_local_ai/
  docker-compose.yml
  .env.example
  infra/
    caddy/Caddyfile
    host/ (setup-avahi.sh, setup-ufw.sh, setup-gpu-drivers.md)
  services/
    model-runner/
      Dockerfile              # server-vulkan base + libglvnd fix
      models/                 # gitignored, GGUF files or download script
    code-exec-manager/
      Dockerfile
      app/main.py              # FastAPI: /sessions, /sessions/{id}/execute, reaper task
      exec-image/Dockerfile     # pre-baked toolbox image used for exec containers themselves
    agent-server/
      Dockerfile               # app code baked in at /app; workspace mounted separately at /data/workspace
      pyproject.toml
      app/
        main.py
        core/config.py
        agent/
          build.py             # create_deep_agent(model=..., backend=FilesystemBackend(...), tools=[execute_code])
          execute_code_tool.py   # plain tool fn -> code-exec-manager HTTP client
          model_client.py       # ChatOpenAI pointed at model-runner
        api/
          chat.py               # threads, messages (REST for history)
          chat_ws.py             # WebSocket endpoint: token + tool-status streaming
          files.py               # upload/download/rename/move/copy/list/mkdir
          media.py                # Range-request byte-streaming for video/audio
          health.py
        db/
          checkpointer.py       # langgraph-checkpoint-postgres wiring
    frontend/
      app.json / eas.json
      Dockerfile                # builds `expo export --platform web`, served by Caddy
      app/                       # Expo Router screens
        (tabs)/chat.tsx
        (tabs)/files.tsx
      components/
        ChatStream.tsx           # WebSocket chat client, works web+native
        MediaPlayer.tsx           # expo-video/expo-audio wrapper, falls back to HTML5 on web
      lib/
        api.ts                   # fetch/WS client pointed at same-origin /api, /ws
  docs/
    ARCHITECTURE.md
    NETWORKING.md
    HOST-CHECKS.md
  scripts/
    e2e/
```

## Project status & roadmap

Delivery is organized into seven milestones, each ending in a **gate** — a scripted + human-verified checkpoint before the next milestone starts:

| Milestone | Value delivered | Gate |
|---|---|---|
| M0 — Foundations | Repo + compose skeleton + host scripts + reverse proxy | Stack boots, placeholder page served |
| M1 — Model runner | Real local GPU inference, tool-calling verdict | `/v1/chat/completions` streams on GPU |
| M2 — Agentic chat | Chat with a deepagents agent (file tools live) from a browser | Browser chat writes a real workspace file |
| M3 — Persistence + files | Threads survive restarts; full file manager UI | Restart-persistence + files round-trip |
| M4 — Code execution | Sandboxed `execute_code` with hard isolation | Agent runs a script on real files; isolation suite green |
| M5 — Media | Video/audio playback with seek from the files UI | Phone-browser seek/scrub |
| M6 — LAN + integration | `homeai.local`, native parity, docs, backup | Full product scenario works from a phone |

Track live progress on the [Milestones](../../milestones) and [Issues](../../issues) pages.

## Working with the GitHub issues

This repo's issue tracker **is** the project plan — there are no separate local planning files. Two pinned reference issues hold the cross-cutting rules that every other issue assumes:

- **[Reference: Shared Conventions & Contracts](../../issues?q=is%3Aissue+label%3Areference)** — binding environment variables, service topology, HTTP/WebSocket API shapes, toolchain pins, path-traversal guard, and testing/definition-of-done rules. If an issue ever conflicts with this one, the reference issue wins — flag the conflict in your PR description.
- **[Reference: Backlog — Ordering, Dependencies & Gates](../../issues?q=is%3Aissue+label%3Areference)** — the full dependency graph, ordered backlog table, parallel work lanes, gate procedure, and risk register.

(Both are pinned at the top of the [Issues](../../issues) list and carry the `reference` label.)

- **Milestones** map 1:1 to the phases in the table above (`M0 - Foundations` … `M6 - LAN + integration`). A milestone isn't done until its gate issue passes.
- **Labels**:
  - `ticket` — a unit of implementation work.
  - `size:S` / `size:M` / `size:L` — rough effort sizing.
  - `gate` — a milestone gate issue (ends with an automated script *and* a human/host checklist). A gate blocks the next milestone's gate from starting, though later-milestone implementation issues may start early if their own dependencies are met.
  - `reference` — cross-cutting rules (conventions, backlog) rather than a unit of work.
- **Dependencies**: each ticket issue body states its `Depends on` / `Blocks` tickets by ID (e.g. `M2-04`, which matches that issue's title prefix). Don't start an issue until its dependencies are closed. The Backlog reference issue has the full dependency graph and the **parallel lanes** — independent tracks of work that can run simultaneously.
- **Definition of done**: each ticket issue lists
  - **Tier A** — automated checks (lint, tests, `docker compose config -q`, any named e2e script) — required to close the issue.
  - **Tier B** (gate issues only) — a human/host checklist (needs the real machine, a phone, or a LAN device). Append these items to `docs/HOST-CHECKS.md` rather than attempting them yourself if you can't reach the physical host.
- **Suggested workflow**: pick the next unblocked issue in dependency order (or any issue in an open parallel lane), implement it to spec, satisfy Tier A locally, open a PR referencing the issue number, and close the issue once Tier A is green (append any Tier B items to `docs/HOST-CHECKS.md` for the PM to verify on-host).
- **Out of scope for v1** (don't build these, even if tempting): TLS/HTTPS, auth, docker-socket-proxy, transcoding, EAS builds, multi-user, GPU queueing, runtime `pip`/`npm` in exec containers, exposing anything to the internet.

## Getting started

Repo scaffold and host scripts (milestone M0) are landing incrementally — see the [issues](../../issues) for what's done. The intended quickstart:

```bash
git clone git@github.com:cucucachu/unnamed_local_ai_server.git
cd unnamed_local_ai_server
cp .env.example .env   # fill in POSTGRES_PASSWORD, RENDER_GID/VIDEO_GID, LAN_SUBNET

./services/model-runner/fetch-model.sh   # downloads the default GGUF quant (~14.6 GB) into services/model-runner/models/

# One-time host prep (idempotent, safe to re-run) — see infra/host/setup-gpu-drivers.md first
sudo infra/host/setup-workspace.sh
sudo infra/host/setup-avahi.sh
sudo infra/host/setup-ufw.sh

# Exec toolbox image — a plain `docker build`, not a compose service (compose
# can't build an image it never runs itself; code-exec-manager, M4-02, spins
# up short-lived containers from this image on demand).
./services/code-exec-manager/build-exec-image.sh

docker compose up -d
```

Everything runs directly on the target Linux host (native, no cloud) — real `docker compose`, the real GPU, the real model. Only phone/LAN-device checks are deferred to a human host checklist ([`docs/HOST-CHECKS.md`](docs/HOST-CHECKS.md)). Networking details (mDNS, firewall, LAN-only isolation) are in [`docs/NETWORKING.md`](docs/NETWORKING.md).

## Backups

**What's covered**: the workspace directory (`WORKSPACE_DIR` — every file the agent/you create, upload, or edit) and the Postgres database (thread/message history). Together these are the only genuinely irreplaceable state this stack holds.

**What's not covered**: model weights (`services/model-runner/models/*.gguf` — multi-GB, re-downloadable any time via `./services/model-runner/fetch-model.sh`, not user data) and `.env` (holds `POSTGRES_PASSWORD` — a secret, deliberately not swept into a backup dir; back it up yourself, out of band, if you want to). v1 backup is a full local mirror only — no off-site/cloud copy, no encryption, no incremental snapshots (see `infra/host/backup-workspace.sh`'s docstring and M6-03's ticket for the explicit out-of-scope list).

**Manual run**:

```bash
sudo infra/host/backup-workspace.sh
```

Mirrors `WORKSPACE_DIR` into `$BACKUP_DIR/workspace` (`rsync -a --delete` — exact mirror, not additive) and, if the stack is up, dumps Postgres into `$BACKUP_DIR/pg/homeai-<date>.sql.gz` (keeps the last 14 by count; skipped with a warning, not an error, if the stack is down). `BACKUP_DIR` defaults to `/srv/homeai/backups` — override in `.env`.

**Automatic daily backups** (03:00, via a systemd timer):

```bash
sudo infra/host/install-backup-timer.sh              # install + enable
sudo infra/host/install-backup-timer.sh --uninstall  # remove
systemctl list-timers homeai-backup.timer            # check it's scheduled
```

**Restoring**:

```bash
# Workspace: rsync back (stop the stack first so nothing's writing to it mid-restore)
docker compose down
sudo rsync -a --delete "$BACKUP_DIR/workspace/" "$WORKSPACE_DIR/"
docker compose up -d

# Postgres: gunzip the dump into a fresh/scratch database via psql
gunzip -c "$BACKUP_DIR/pg/homeai-<date>.sql.gz" | docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

## License

Not yet decided — this is a personal home-server project. No license is currently granted for reuse.
