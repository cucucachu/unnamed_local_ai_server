"""Client-facing routes, routed by Caddy (docs/PLATFORM.md §3 "HTTP routing").

- `auth`: `/api/auth/*`, unauthenticated (login, setup, invite accept, status).
- `platform`: `/api/platform/*`, behind Caddy `forward_auth`.

Both are empty until M10-03 onward.
"""
