# Stage name/`/out` output path are a contract with the final stage below
# (and were with M2-05, which replaced this stage's placeholder body with
# the real `expo export` build) — keep both stable.
FROM node:22-alpine AS frontend-build
WORKDIR /app
COPY services/frontend/package.json services/frontend/package-lock.json ./
RUN npm ci
COPY services/frontend/ ./
# Web export defaults to a `dist/` output directory (verified locally with
# `npx expo export --platform web`); `web.output: "single"` in app.json
# makes it a single-page app, matching Caddy's `try_files {path} /index.html`
# SPA fallback in infra/caddy/Caddyfile.
RUN npx expo export --platform web && mkdir -p /out && cp -r dist/. /out/

FROM caddy:2-alpine
COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend-build /out /srv/www
