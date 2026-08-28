# Home AI Agent

A private, local, always-on AI agent for your home network — a chat assistant with real file access and sandboxed code execution, plus a media-aware file browser, all served from one address to every device on your WiFi.

> **Status: planning / pre-implementation.** The architecture and full delivery backlog are written; no services are built yet. See [Project status & roadmap](#project-status--roadmap) below.

## Overview

This repo builds a personal AI agent that lives on your own hardware and your own network. It's a single system, reachable from any device on your WiFi at one address, that combines:

- A private, local chat assistant — no cloud API, no internet dependency, no per-message cost.
- A real file-management partner that can create, edit, organize, rename, move, and clean up files for you, and remembers the state of your files across sessions because it works on the same persistent disk every time.
- The ability to actually run code to get things done (batch-processing files, writing small scripts, transforming data), contained so it can't damage the machine it runs on.
- A media-aware file browser so you can play back your videos and audio straight from the same interface, on your phone or your laptop.

In short: a private, always-on "computer-use" assistant for your home network, with hands (file access) and a sandboxed toolbox (code execution), instead of just a chat window.

## Why

- **Your data stays yours.** Files, conversations, and anything the agent touches never leave your network.
- **No usage limits, no subscription, no metering.** Once it's running, ten things or ten thousand things cost the same: electricity.
- **It can actually do things, not just describe them.** It edits your real filesystem directly and shows you the result in the same UI.
- **It's safe to let it run code.** Code execution happens in a locked-down, disposable container that can touch your files but nothing else on the machine.
- **It's available anywhere on your network.** Same address, same app, from your phone on the couch or your laptop at the desk — including media playback.
- **It's a foundation, not a toy.** Swappable model, swappable quant, a real API layer, a real sandbox boundary — built to grow instead of dead-ending as a weekend hack.

**Who it's for**: you, on your home network. A single-user, trusted-LAN tool, not a multi-tenant product, not exposed to the internet.

**What using it looks like**:

- *"Organize my Downloads folder — group by type, get rid of obvious junk."*
- *"Rename these photos to their capture dates and put them in folders by month."*
- *"Summarize these PDFs and save the summary as a new file next to them."*
- *"Convert this batch of videos to a smaller format."*
- *"Pull up that video from last week's project on my phone."*
- *"Help me write and test a small script."*

## Guiding principles

- **Local-first, always.** Inference, file storage, and code execution all happen on this machine.
- **Give it real capability, then contain the risk.** Genuine, persistent file access; arbitrary code execution isolated behind a hard boundary instead of being restricted into uselessness.
- **One address, every device.** `homeai.local` (or wherever it lands) is the whole interface, from any device on the network.
- **Minimum viable now, room to grow later.** Get the core loop (chat, files, code, media) working end to end, with a documented path to add polish and hardening later.

## Architecture at a glance

- **Model**: Gemma 4 26B-A4B (MoE), served locally via `llama.cpp` (Vulkan) on the host iGPU.
- **Agent framework**: `deepagents` on LangGraph — direct filesystem tools + a sandboxed `execute_code` tool.
- **Code execution**: isolated, network-less, disposable containers with no path to the agent's own code, secrets, or the Docker socket.
- **Frontend**: Expo (React Native + Expo Router), one codebase exported to web, iOS, and Android.
- **Transport**: WebSocket for chat token/tool-status streaming; HTTP Range requests for media seek/scrub.
- **Networking**: LAN-only, no auth (v1), reached via mDNS (`homeai.local`) behind a Caddy reverse proxy.

Full diagrams, component design, and environment-variable/API contracts live in [`PLAN.md`](./PLAN.md) and [`tickets/CONVENTIONS.md`](./tickets/CONVENTIONS.md).

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

Track live progress on the [Milestones](../../milestones) and [Issues](../../issues) pages. The full dependency graph and parallel work lanes are in [`tickets/BACKLOG.md`](./tickets/BACKLOG.md).

## Working with the GitHub issues

Every ticket under [`tickets/`](./tickets) has a matching GitHub issue — the issues are the working/tracking copy; the ticket files remain the canonical spec (context, full acceptance criteria, out-of-scope notes). If they ever drift, treat the `tickets/*.md` file as the source of truth.

- **Milestones** map 1:1 to the phases above (`M0 - Foundations` … `M6 - LAN + integration`). A milestone isn't done until its gate issue passes.
- **Labels**:
  - `ticket` — every issue imported from `tickets/`.
  - `size:S` / `size:M` / `size:L` — rough effort sizing, from the ticket's own estimate.
  - `gate` — a milestone gate issue (ends with an automated script *and* a human/host checklist). A gate blocks the next milestone's gate from starting, though later-milestone implementation tickets may start early if their own dependencies are met.
- **Dependencies**: each issue body states its `Depends on` / `Blocks` tickets. Don't start an issue until its dependencies are closed. [`tickets/BACKLOG.md`](./tickets/BACKLOG.md) has the full dependency graph, an ordered backlog table, and the **parallel lanes** (independent tracks of work an agent/dev fleet can run simultaneously).
- **Definition of done**: each issue lists
  - **Tier A** — automated checks (lint, tests, `docker compose config -q`, any named e2e script) — required to close the issue.
  - **Tier B** (gate issues only) — a human/host checklist (needs the real machine, a phone, or a LAN device). Append these items to `docs/HOST-CHECKS.md` rather than attempting them yourself if you can't reach the physical host.
- **Shared contracts are binding**: [`tickets/CONVENTIONS.md`](./tickets/CONVENTIONS.md) defines the environment variables, service topology, HTTP/WebSocket API shapes, toolchain pins, and commit-message format (`M2-04: short imperative summary`) every ticket assumes. If an issue ever conflicts with `CONVENTIONS.md`, `CONVENTIONS.md` wins — flag the conflict in your PR description.
- **Suggested workflow**: pick the next unblocked issue in `BACKLOG.md` order (or any issue in an open parallel lane), implement it to spec, satisfy Tier A locally, open a PR referencing the issue number, and close the issue once Tier A is green (append any Tier B items to `docs/HOST-CHECKS.md` for the PM to verify on-host).
- **Out of scope for v1** (don't build these, even if tempting): TLS/HTTPS, auth, docker-socket-proxy, transcoding, EAS builds, multi-user, GPU queueing, runtime `pip`/`npm` in exec containers, exposing anything to the internet — see `tickets/CONVENTIONS.md` §10.

## Documentation map

| Doc | Purpose |
|---|---|
| [`PLAN.md`](./PLAN.md) | Architecture decisions, system diagrams, repository layout, delivery-phase ticket summaries |
| [`tickets/BACKLOG.md`](./tickets/BACKLOG.md) | Ordering, dependency graph, parallel lanes, gate procedure, risk register |
| [`tickets/CONVENTIONS.md`](./tickets/CONVENTIONS.md) | Binding shared contracts: env vars, service topology, API shapes, toolchain pins, testing/DoD rules |
| [`tickets/*.md`](./tickets) | Individual ticket specs (source of truth; mirrored as GitHub issues) |
| `docs/HOST-CHECKS.md` *(created in M0)* | Human/host verification checklist, appended to by gate tickets |

## Getting started

The stack isn't scaffolded yet (that's milestone M0). Once it lands, the intended quickstart is:

```bash
git clone git@github.com:cucucachu/unnamed_local_ai_server.git
cd unnamed_local_ai_server
cp .env.example .env   # fill in POSTGRES_PASSWORD and any host-specific values
# run the host setup scripts under infra/host/ (avahi, ufw, GPU driver check)
docker compose up -d
```

Everything runs directly on the target Linux host (native, no cloud); see [`tickets/CONVENTIONS.md` §1](./tickets/CONVENTIONS.md) for the assumed execution environment.

## License

Not yet decided — this is a personal home-server project. No license is currently granted for reuse.
