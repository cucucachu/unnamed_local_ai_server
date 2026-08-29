# Stage names/paths are a contract with M2-05, which replaces this stage's
# body with the real `expo export` build. Keep the stage name `frontend-build`
# and the `/out` output path stable.
FROM node:22-alpine AS frontend-build
RUN mkdir -p /out && echo '<h1>homeai: frontend not built yet</h1>' > /out/index.html

FROM caddy:2-alpine
COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend-build /out /srv/www
