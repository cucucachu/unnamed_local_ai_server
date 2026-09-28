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
- [Accounts and recovery](#accounts-and-recovery)
- [Backups](#backups)
- [License](#license)

## Overview

This repo builds a personal AI agent that lives on your own hardware and your own network. It's a single system, reachable from any device on your WiFi at one address, that combines:

- A private, local chat assistant — no cloud API, no internet dependency, no per-message cost.
- A real file-management partner that can create, edit, organize, rename, move, and clean up files for you, and remembers the state of your files across sessions because it works on the same persistent disk every time.
- The ability to actually run code to get things done (batch-processing files, writing small scripts, transforming data), contained so it can't damage the machine it runs on.
- A media-aware file browser so you can play back your videos and audio straight from the same interface, on your phone or your laptop.

In short: a private, always-on "computer-use" assistant for your home network, with hands (file access) and a sandboxed toolbox (code execution), instead of just a chat window.

**Who it's for**: you, on your home network. A household LAN tool. Remote
access is WireGuard (Settings → Remote access; `docs/NETWORKING.md`), not
exposing HTTP to the internet.

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

- **Local-first, always.** Inference, file storage, and code execution all happen on this machine. Internet access (if any) is incidental — for pulling container images or model files — never required day to day. The one product exception is **opt-in voice input**: the browser's speech recognition (and the phone keyboard's dictation) typically send audio to the platform vendor (Google/Apple). This is the only place the product touches a third-party service, and it is opt-in per use. Expo Go has no in-app mic button — use the keyboard's built-in dictation.

- **Search depends on the public web answering, not on a third-party search service.** The agent's web search runs through a self-hosted metasearch engine (SearXNG) that queries public search engines directly — no hosted search API, no API key, nothing leaves this machine except the outbound query itself. That said, the *results* still depend on real websites out there answering those queries, same as any page the agent reads with `/fetch` — this stack owns the search step, not the internet's willingness to respond.
- **Give it real capability, then contain the risk.** Genuine, persistent file access, because that's the point of the tool. Anything riskier — arbitrary code execution — is isolated behind a hard boundary instead of being restricted into uselessness.
- **One address, every device.** `homeai.local` (or wherever it lands) is the whole interface, from any device on the network.
- **Minimum viable now, room to grow later.** Get the core loop (chat, files, code, media) working end to end, with a documented path to add polish and hardening later without rearchitecting.

## Architecture

Native Linux host + Docker Compose, five services behind one reverse
proxy: **caddy** (LAN entry point + static Expo web build), **agent-server**
(FastAPI + `deepagents`, direct persistent filesystem access via a
bind-mounted files directory), **model-runner** (`llama.cpp` Vulkan build serving
Gemma 4 on the iGPU), **code-exec-manager** (the sole `docker.sock` holder,
spinning up locked-down, session-scoped containers for the agent's
`execute_code` tool), and **postgres** (thread/checkpoint state). Code
execution is deliberately isolated from agent-server's own app code,
secrets, and every other service — see the isolation boundary described
below. HTTP/HTTPS stay LAN-only by topology + host firewall (local HTTPS
via Caddy's internal CA does not replace the LAN trust model). Remote
access is WireGuard (`docs/NETWORKING.md`), not public HTTP.

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
      Dockerfile               # app code baked in at /app; files directory mounted separately at /data/files
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
  examples/
    apps/grocery-list/         # reference app + first template (install: examples/apps/README.md)
  scripts/
    e2e/
```

## Project status & roadmap

Delivery is organized into two stages, each broken into milestones that end in a **gate** — a scripted + human-verified checkpoint before the next milestone starts.

**Stage 1 — v1** (seven milestones):

| Milestone | Value delivered | Gate |
|---|---|---|
| M0 — Foundations | Repo + compose skeleton + host scripts + reverse proxy | Stack boots, placeholder page served |
| M1 — Model runner | Real local GPU inference, tool-calling verdict | `/v1/chat/completions` streams on GPU |
| M2 — Agentic chat | Chat with a deepagents agent (file tools live) from a browser | Browser chat writes a real file to the files directory |
| M3 — Persistence + files | Threads survive restarts; full file manager UI | Restart-persistence + files round-trip |
| M4 — Code execution | Sandboxed `execute_code` with hard isolation | Agent runs a script on real files; isolation suite green |
| M5 — Media | Video/audio playback with seek from the files UI | Phone-browser seek/scrub |
| M6 — LAN + integration | `homeai.local`, native parity, docs, backup | Full product scenario works from a phone |

**Stage 2 — agent harness + chat experience** (three further milestones; see the
[Stage 2 backlog reference](../../issues/72) for the full dependency graph, parallel lanes, and
risk register):

| Milestone | Value delivered | Gate |
|---|---|---|
| M7 — Web research (read-only) | Agent can search and read the public web through an infrastructure-enforced GET/HEAD-only egress path; nothing else in the stack can reach the internet | G7: agent researches a question end-to-end; egress + isolation suites green |
| M8 — Agent controls | Stop, edit/resend (truncate + fork), configurable human-in-the-loop approvals, and (spike-gated) model thinking | G8: stop / approve-reject / edit (truncate + fork) from a browser |
| M9 — Chat experience | Markdown, turn activity panel with runtime, file deep links, Android keyboard fix, local HTTPS, voice input | G9: full chat experience from a phone over HTTPS + Expo Go |

**Stage 3 — the agentic OS** (multi-user household platform + agent-built apps; design, decisions,
and cross-service contracts in [`docs/PLATFORM.md`](docs/PLATFORM.md)):

| Milestone | Value delivered | Gate |
|---|---|---|
| M10 — Identity & spaces | Accounts, sessions, bootstrap, invites, spaces + membership, auth on every route, threads owned by users | G10: two users on separate devices each see only their own threads; admin manages users and spaces |
| M11 — Platform owns storage | Platform files API (`/personal`, `/spaces/<slug>`), delegation tokens, agent file tools + exec as the user, Postgres roles, tenancy suite | G11: cross-user isolation suite green across UI, agent tools, and exec |
| M12 — App runtime | App package format, registry, per-instance SQLite with computed migrations, sandboxed builds, SDK, sandboxed runtime (web + Expo Go) | G12: a hand-written reference app runs in personal and shared spaces on web and phone |
| M13 — Agent builds apps | Apps in the files tree + git, app tools, templates, model authoring eval, ask-the-agent panel | G13: "make me a grocery list app" end-to-end with the real model |
| M14 — Sharing & system apps | Publish/install/update/fork, Home launcher, Settings/Files/Chat as system apps, cross-app exports | G14: family installs a published app; planner reads calendar exports |
| M15 — Remote access & devices | WireGuard, domain mode, passkeys, opt-in public HTTPS, host app dev build + device pairing | G15: phone reaches the box over WireGuard off-LAN; public mode refuses enrollment |

Stage 3 deliberately lifts several v1 out-of-scope items (auth, multi-user, EAS/dev builds, and — opt-in only — internet exposure); `docs/PLATFORM.md` §2 is the record of what changed and why.

Track live progress on the [Milestones](../../milestones) and [Issues](../../issues) pages.

## Working with the GitHub issues

This repo's issue tracker **is** the project plan — there are no separate local planning files. Every backlog ticket across Stage 1's seven milestones is closed; Stage 2 (M7–M9) is in progress. The [Issues](../../issues) page is the delivery history.

The binding technical contracts every ticket built against — environment variables, service topology, HTTP/WebSocket API shapes, the path-traversal guard — live in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#3-contracts). The full dependency graph, ordered backlog table, parallel work lanes, and risk register that governed build order are preserved on the closed [Reference: Backlog — Ordering, Dependencies & Gates](../../issues/35) issue (Stage 1) and the [Reference: Stage 2 backlog](../../issues/72) issue (M7–M9).

- **Milestones** map 1:1 to the phases in the tables above (`M0 - Foundations` … `M9 - Chat experience`). A milestone was done once its gate issue passed.
- **Labels**:
  - `ticket` — a unit of implementation work.
  - `size:S` / `size:M` / `size:L` — rough effort sizing.
  - `gate` — a milestone gate issue (ends with an automated script *and* a human/host checklist). A gate blocked the next milestone's gate from starting, though later-milestone implementation issues could start early if their own dependencies were met.
  - `reference` — cross-cutting rules (conventions, backlog) rather than a unit of work.
- **Dependencies**: each ticket issue body states its `Depends on` / `Blocks` tickets by ID (e.g. `M2-04`, which matches that issue's title prefix).
- **Definition of done**: each ticket issue lists
  - **Tier A** — automated checks (lint, tests, `docker compose config -q`, any named e2e script) — required to close the issue.
  - **Tier B** (gate issues only) — a human/host checklist (needs the real machine, a phone, or a LAN device). Appended to `docs/HOST-CHECKS.md`.
  - **Out of scope for v1** (don't build these, even if tempting): forcing HTTPS / HSTS, public WAN HTTP/HTTPS (M15-05), auth, docker-socket-proxy, transcoding, EAS builds, multi-user, GPU queueing, runtime `pip`/`npm` in exec containers, exposing HTTP/HTTPS to the internet. Optional real certificates via ACME DNS-01 (DuckDNS, no inbound 80/443) shipped in M15-03; `https://homeai.local` (Caddy internal CA) remains. Stage 3's sanctioned remote path is WireGuard (M15-01), not a public website.

## Getting started

Repo scaffold and host scripts (milestone M0) are landing incrementally — see the [issues](../../issues) for what's done. The intended quickstart:

```bash
git clone git@github.com:cucucachu/unnamed_local_ai_server.git
cd unnamed_local_ai_server
cp .env.example .env   # fill in POSTGRES_PASSWORD, PLATFORM_* secrets, RENDER_GID/VIDEO_GID, LAN_SUBNET

./services/model-runner/fetch-model.sh   # downloads the default GGUF quant (~14.6 GB) into services/model-runner/models/

# One-time host prep (idempotent, safe to re-run) — see infra/host/setup-gpu-drivers.md first
sudo infra/host/setup-files.sh
sudo infra/host/setup-avahi.sh
sudo infra/host/setup-ufw.sh

# Exec toolbox image — a plain `docker build`, not a compose service (compose
# can't build an image it never runs itself; code-exec-manager, M4-02, spins
# up short-lived containers from this image on demand).
./services/code-exec-manager/build-exec-image.sh

docker compose up -d
```

Everything runs directly on the target Linux host (native, no cloud) — real `docker compose`, the real GPU, the real model. Only phone/LAN-device checks are deferred to a human host checklist ([`docs/HOST-CHECKS.md`](docs/HOST-CHECKS.md)). Networking details (mDNS, firewall, LAN-only isolation) are in [`docs/NETWORKING.md`](docs/NETWORKING.md).

**If an AI coding agent is working in this repo**, see [`AGENTS.md`](AGENTS.md) before running `docker compose`/`scripts/e2e/*`/`scripts/verify_*` — a sandboxed shell tool usually can't reach the real Docker daemon or network directly, and needs explicit elevated permission (or the user running the command) to verify anything for real.

## Accounts and recovery

Stage 3 (M10) adds accounts, owned by the `platform` service ([`docs/PLATFORM.md`](docs/PLATFORM.md) §4; API contract in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §3 "Platform API").

**First admin (bootstrap)**: on first start the platform prints a one-time setup code and keeps it in its data volume until someone uses it:

```bash
docker compose logs platform | grep "SETUP CODE"
docker compose exec platform cat /data/platform/setup-code
```

Whoever enters that code on the setup screen (`POST /api/auth/setup`) becomes the **bootstrap admin**; pre-Stage-3 threads and files will be assigned to them. After that, setup is closed for good and the code file is deleted. Everyone else joins by admin invite (single-use link, 7 days).

**Recovery CLI** (anyone with Docker on the host is root anyway, so this is the physical-access path). It runs inside the running container with the image's own Python — not `uv run`, which would try to write to the read-only root:

```bash
docker compose exec platform python -m app.cli list-users
docker compose exec platform python -m app.cli create-user alice --role admin     # prompts for a password
docker compose exec platform python -m app.cli reset-password alice [--clear-totp] # revokes all their sessions
docker compose exec platform python -m app.cli set-role alice member               # refuses to remove the last admin
docker compose exec platform python -m app.cli disable-user alice                  # revokes their sessions
docker compose exec platform python -m app.cli enable-user alice
docker compose exec platform python -m app.cli list-spaces [--json]                 # every space, its GID and members
docker compose exec platform python -m app.cli create-space family --name Family --owner alice
docker compose exec platform python -m app.cli add-member family bob --role viewer  # owner|editor|viewer
docker compose exec platform python -m app.cli register-app alice /personal/Apps/groceries  # as alice; prints the app id
docker compose exec platform python -m app.cli install-app alice <app-id> [--space family]  # default: alice's personal space
docker compose exec platform python -m app.cli list-apps [--json]

# Non-interactive (scripts): first line of stdin is the password; note -T.
printf '%s\n' "$PW" | docker compose exec -T platform python -m app.cli create-user e2e-bob --password-stdin
```

Every user gets a personal space when they're created; shared spaces are created in the app or with `create-space`. Each space's data lives under `SPACES_DIR` (default `/srv/homeai/spaces`) in `<space_id>/`, owned `root:<space gid>` with mode `2770`, so your host login can list `SPACES_DIR` but not look inside a space — use `docker compose exec platform ls -ln /data/spaces/<space_id>`.

Users created with the CLI **don't** complete bootstrap — the setup code keeps working until someone uses it. Errors print `error: <code>` and exit 1.

## Backups

**What's covered**: the spaces directory (`SPACES_DIR` — every personal and shared space's files, whatever the agent or you create, upload, or edit), the legacy files directory (`FILES_DIR` — pre-M11 files until the platform migrates them into the first admin's personal space), both Postgres databases (`homeai`: thread/message history; `homeai_platform`: users, sessions, invites, spaces and memberships), and the `platform-data` volume (the platform's token-signing key, plus the setup code until the first admin exists). Together these are the only genuinely irreplaceable state this stack holds.

**What's not covered**: model weights (`services/model-runner/models/*.gguf` — multi-GB, re-downloadable any time via `./services/model-runner/fetch-model.sh`, not user data) and `.env` (holds `POSTGRES_PASSWORD` — a secret, deliberately not swept into a backup dir; back it up yourself, out of band, if you want to). v1 backup is a full local mirror only — no off-site/cloud copy, no encryption, no incremental snapshots (see `infra/host/backup-files.sh`'s docstring and M6-03's ticket for the explicit out-of-scope list).

**Manual run**:

```bash
sudo infra/host/backup-files.sh
```

Mirrors `SPACES_DIR` into `$BACKUP_DIR/spaces` (owners and modes kept, so it's as private as the original) and `FILES_DIR` into `$BACKUP_DIR/files` (`rsync -a --delete` — exact mirrors, not additive); if the stack is up, dumps Postgres into `$BACKUP_DIR/pg/homeai-<date>.sql.gz` and `$BACKUP_DIR/pg/homeai_platform-<date>.sql.gz` (keeps the last 14 of each by count; skipped with a warning, not an error, if the stack is down); and mirrors the `homeai_platform-data` volume into `$BACKUP_DIR/platform-data` (root-only, `0700` — it contains the private signing key; works with the stack down too). `BACKUP_DIR` defaults to `/srv/homeai/backups` — override in `.env`.

**Automatic daily backups** (03:00, via a systemd timer):

```bash
sudo infra/host/install-backup-timer.sh              # install + enable
sudo infra/host/install-backup-timer.sh --uninstall  # remove
systemctl list-timers homeai-backup.timer            # check it's scheduled
```

**Restoring**:

```bash
# Files: rsync back (stop the stack first so nothing's writing to it mid-restore)
docker compose down
sudo rsync -a --delete "$BACKUP_DIR/spaces/" "$SPACES_DIR/"
sudo rsync -a --delete "$BACKUP_DIR/files/" "$FILES_DIR/"
docker compose up -d

# Postgres: gunzip the dump into a fresh/scratch database via psql, then
# re-run db-init: it hands anything the restore left owned by the superuser
# (e.g. a dump from before M11-04) to agent-server's `agent` role
gunzip -c "$BACKUP_DIR/pg/homeai-<date>.sql.gz" | docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
docker compose run --rm db-init

# Platform database: same, into an empty homeai_platform (db-init recreates
# the database and the `platform` role the dump's OWNER statements expect)
docker compose stop platform
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c 'DROP DATABASE homeai_platform'
docker compose run --rm db-init
gunzip -c "$BACKUP_DIR/pg/homeai_platform-<date>.sql.gz" | docker compose exec -T postgres psql -U "$POSTGRES_USER" -d homeai_platform
docker compose start platform

# platform-data volume (signing key): copy back into the volume's host directory
docker compose stop platform
sudo rsync -a --delete "$BACKUP_DIR/platform-data/" "$(docker volume inspect -f '{{.Mountpoint}}' homeai_platform-data)/"
docker compose start platform
```

Restore the platform database and `platform-data` from the same backup run: sessions and identity tokens are only valid against the signing key they were issued with (a mismatched key just logs everyone out).

## License

Not yet decided — this is a personal home-server project. No license is currently granted for reuse.
