# Stage name/`/out` output path are a contract with the final stage below
# (and were with M2-05, which replaced this stage's placeholder body with
# the real `expo export` build) — keep both stable.
FROM node:22-alpine AS frontend-build
WORKDIR /app
# The app host imports `@homeai/sdk/host` (`file:../../packages/homeai-sdk`,
# which from /app is /packages/homeai-sdk); only its sources are used.
COPY packages/homeai-sdk/ /packages/homeai-sdk/
COPY services/frontend/package.json services/frontend/package-lock.json ./
RUN npm ci
COPY services/frontend/ ./
# Web export defaults to a `dist/` output directory (verified locally with
# `npx expo export --platform web`); `web.output: "single"` in app.json
# makes it a single-page app, matching Caddy's `try_files {path} /index.html`
# SPA fallback in infra/caddy/Caddyfile.
RUN npx expo export --platform web && mkdir -p /out && cp -r dist/. /out/

# The app sandbox runtime (packages/homeai-sdk), one IIFE per SDK version,
# served at /app-runtime/<sdk>/runtime.js for app hosts to inline into the
# sandbox document (docs/PLATFORM.md §7 "Runtime and bridge").
FROM node:22-alpine AS runtime-build
WORKDIR /sdk
COPY packages/homeai-sdk/package.json packages/homeai-sdk/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY packages/homeai-sdk/ ./
RUN node build.mjs --out /out/app-runtime/1

FROM caddy:2-alpine
COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend-build /out /srv/www
COPY --from=runtime-build /out/app-runtime /srv/app-runtime
