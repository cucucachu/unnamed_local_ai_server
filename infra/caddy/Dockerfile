# xcaddy + DuckDNS first so `docker build --target caddy-dns` (used by
# scripts/verify_caddy_domain.sh) does not run the frontend/runtime stages.
FROM caddy:2-builder-alpine AS caddy-builder
RUN xcaddy build --with github.com/caddy-dns/duckdns

# Plugin-enabled alpine binary, no frontend — throwaway validate image.
FROM caddy:2-alpine AS caddy-dns
COPY --from=caddy-builder /usr/bin/caddy /usr/bin/caddy

# Stage name/`/out` output path are a contract with the final stage below
# (and were with M2-05, which replaced this stage's placeholder body with
# the real `expo export` build) — keep both stable.
FROM node:22-alpine AS frontend-build
# The app host imports `@homeai/sdk/host` (`file:../../packages/homeai-sdk`;
# only its sources are used). The lockfile keys that link by its path
# relative to the frontend, so the stage keeps the repo's layout.
WORKDIR /repo/services/frontend
COPY packages/homeai-sdk/ /repo/packages/homeai-sdk/
COPY services/frontend/modules/homeai-device-key/ /repo/services/frontend/modules/homeai-device-key/
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
COPY --from=caddy-builder /usr/bin/caddy /usr/bin/caddy
COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
COPY infra/caddy/entrypoint.sh /entrypoint.sh
COPY infra/lib/valid_hostname.sh /usr/local/lib/homeai-valid-hostname.sh
RUN chmod 755 /entrypoint.sh \
	&& printf '%s\n' '# domain mode off' > /etc/caddy/domain.caddy \
	&& printf '%s\n' '# wireguard via unset' > /etc/caddy/via.caddy
COPY --from=frontend-build /out /srv/www
COPY --from=runtime-build /out/app-runtime /srv/app-runtime
ENTRYPOINT ["/entrypoint.sh"]
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
