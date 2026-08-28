# M0-03 — Caddy reverse proxy + placeholder frontend

**Milestone**: M0 · **Size**: S · **Depends on**: M0-01 · **Blocks**: M2-05

## Context

Caddy is the single published entry point (port 80): serves the (future) Expo web export as
static files and proxies `/api` + `/ws` to agent-server. Until the frontend exists, it serves a
placeholder page so the "one address" property is verifiable from day one. PLAN.md P5-1.

## Spec

1. **`infra/caddy/Caddyfile`**:

```
:80 {
	encode gzip

	handle /api/* {
		reverse_proxy agent-server:8000
	}
	handle /ws/* {
		reverse_proxy agent-server:8000
	}
	handle {
		root * /srv/www
		try_files {path} /index.html
		file_server
	}
}
```

(Caddy's `reverse_proxy` handles WebSocket upgrades automatically — no extra config needed.)

2. **`infra/caddy/Dockerfile`** — multi-stage, build context = repo root (so it can later copy
   the frontend build):
   - Stage `frontend-build`: `FROM node:22-alpine`; **for now** just
     `RUN mkdir -p /out && echo '<h1>homeai: frontend not built yet</h1>' > /out/index.html`.
     M2-05 replaces this stage's body with the real `expo export` build; keep the stage name
     and `/out` output path stable — that's this ticket's contract with M2-05.
   - Final stage: `FROM caddy:2-alpine`; `COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile`;
     `COPY --from=frontend-build /out /srv/www`.

3. **compose**: add `caddy` service — `build: { context: ., dockerfile: infra/caddy/Dockerfile }`,
   `ports: ["80:80"]`, `networks: [homeai-net]`, `restart: unless-stopped`. Do **not** add a
   `depends_on` for agent-server (it doesn't exist yet; Caddy 502s on `/api` until M2-01, which
   is fine).

## Out of scope

The real frontend build (M2-05); TLS; auth.

## Acceptance criteria (Tier A)

- [ ] `docker compose config -q` passes; `caddy` is the **only** service with `ports:`.
- [ ] `docker compose up -d caddy` → `curl -s http://localhost/` returns the placeholder HTML.
- [ ] `curl -s -o /dev/null -w '%{http_code}' http://localhost/api/health` returns `502`
      (proxy wired, upstream absent).
- [ ] `curl -s http://localhost/nonexistent-route` returns the placeholder HTML (SPA fallback
      via `try_files`).

## Tier B (append to docs/HOST-CHECKS.md under M0)

- [ ] Phone on WiFi: `http://homeai.local` shows the placeholder page. **(GATE G0)**
