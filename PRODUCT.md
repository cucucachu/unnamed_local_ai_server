# Home AI Agent — Product Overview

## What we're building

A personal AI agent that lives on your own hardware and your own network. It's a single system, reachable from any device on your WiFi at one address, that combines:

- A private, local chat assistant (no cloud API, no internet dependency, no per-message cost).
- A real file-management partner that can create, edit, organize, rename, move, and clean up files for you — and remembers the state of your files across sessions, because it's working on the same persistent disk every time.
- The ability to actually run code to get things done (batch-processing files, writing small scripts, transforming data), in a way that's contained so it can't damage the machine it runs on.
- A media-aware file browser so you can play back your videos and audio straight from the same interface, on your phone or your laptop.

In short: it's a private, always-on "computer-use" assistant for your home network, with hands (file access) and a sandboxed toolbox (code execution), instead of just a chat window.

## Why you want it

- **Your data stays yours.** Files, conversations, and anything the agent touches never leave your network. Nothing is uploaded to a third party to get a useful answer or to have a file edited.
- **No usage limits, no subscription, no metering.** Once it's running, asking it to do ten things or ten thousand things costs the same: electricity. The hardware (Ryzen AI 9 HX 370, 96GB RAM, Radeon 890M) is already sitting there capable of running a genuinely strong model.
- **It can actually do things, not just describe them.** Most chat assistants can tell you how to rename 200 files by date — this one can just do it, directly on your real filesystem, and show you the result in the same UI.
- **It's safe to let it run code.** Code execution happens in a locked-down, disposable container that can touch your files but nothing else on the machine — so "let the agent write and run a script" doesn't carry the same risk it would if it were running directly on your host.
- **It's available anywhere on your network, not just at one desk.** Same address, same app, from your phone on the couch or your laptop at the desk — including playing back media you've stored on it.
- **It's a foundation, not a toy.** The architecture is deliberately modular (swappable model, swappable quant, a real API layer, a real sandbox boundary) so it can grow — more tools, more automation, more hardening — instead of being a weekend hack that dead-ends.

## Who it's for

You, on your home network. A single-user, trusted-LAN tool — not a multi-tenant product, not something exposed to the internet. It assumes you're comfortable with (or want to get comfortable with) running your own infrastructure in exchange for full control and zero recurring cost.

## What using it looks like

- "Organize my Downloads folder — group by type, get rid of obvious junk." It looks at the files, proposes and makes the changes.
- "Rename these photos to their capture dates and put them in folders by month." It writes and runs a small script against your real files.
- "Summarize these PDFs and save the summary as a new file next to them." It reads, writes, and reports back.
- "Convert this batch of videos to a smaller format." It runs the conversion in its sandbox, writes the results back to your files.
- "Pull up that video from last week's project on my phone." You open the same address on your phone and play it back with normal seek/scrub.
- "Help me write and test a small script." It writes the script to disk, runs it in its sandbox, iterates based on the output — without ever touching anything outside your designated workspace.

## Guiding principles

- **Local-first, always.** Inference, file storage, and code execution all happen on this machine. Internet access (if any) is incidental — for pulling container images or model files — never required for the tool to function day to day.
- **Give it real capability, then contain the risk.** The agent gets genuine, persistent access to your files because that's the point of the tool. Anything riskier — arbitrary code execution — is isolated behind a hard boundary instead of being restricted into uselessness.
- **One address, every device.** You shouldn't have to think about which app or which machine — homeai.local (or whatever address it lands on) is the whole interface, from any device on the network.
- **Minimum viable now, room to grow later.** The first version is intentionally plain — get the core loop (chat, files, code, media) working end to end — with a clear, documented path to add polish, hardening, and features later without rearchitecting.

## Companion document

See [`PLAN.md`](./PLAN.md) for the technical architecture, component design, and delivery tickets that implement this product.
