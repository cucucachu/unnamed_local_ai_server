# Notes for AI coding agents

This repo's `scripts/e2e/*.sh`/`*.mjs` scripts, `scripts/verify_*.sh`, and any
`docker compose ...` invocation talk to the **real Docker daemon, real GPU,
real model weights, and real internet egress** on this host. If you're an AI
coding agent (e.g. Cursor) working in a sandboxed shell tool, that sandbox
very likely can't reach these directly by default — and the failures it
produces are easy to misread as real host/product bugs when they're not:

- `Cannot connect to the Docker daemon at unix:///var/run/docker.sock` —
  usually just means the sandbox's `/run` is isolated from the host's, not
  that `dockerd` isn't running. Check `journalctl -u docker.service -b` (also
  sandbox-restricted, but readable) or ask the user to run `docker compose
  ps` themselves before concluding the daemon is down.
- `systemctl ...` failing with `Failed to connect to system scope bus...
  Host is down` — the sandbox blocking the D-Bus socket, not evidence the
  host isn't using systemd (`/proc/1/comm` reads `systemd` even when this
  happens).
- `npm`/`pip`/etc. `FETCH_ERROR` against a public registry — the sandbox's
  network allowlist, not a real connectivity problem.

**What to do about it:**

1. Prefer requesting the specific escalated permission your tool supports
   (e.g. Cursor's `full_network` / `all` shell permissions) and let the user
   approve it, rather than assuming a real failure from a sandboxed run.
2. If that's not available or the user declines, ask the user to run the
   exact command themselves in their own terminal and paste back the output
   — don't guess at, or narrate as fact, results you couldn't actually
   observe.
3. Several `scripts/e2e/*.sh` header comments already flag steps that
   fundamentally require live Docker + real internet + a real GPU (e.g.
   `web_research_smoke.sh`'s own comment: "NOT runnable offline/in a sandbox
   with no live docker+internet, by design"). That's a different, permanent
   constraint (the check's whole point is proving something real happened)
   — distinct from the sandbox-visibility issues above, which usually go
   away once the right permission is granted.
