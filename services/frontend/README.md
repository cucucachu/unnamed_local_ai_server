# Frontend (Expo)

Single Expo codebase (web + iOS + Android) for the Home AI Agent UI. This is
the M2-05 scaffold: routing shell, the shared API/WS client, and the web
export wired into the Caddy image. **No real screens yet** — chat lands in
M2-06, the files UI in M3-05.

- **Expo SDK 57** (scaffolded via `npx create-expo-app@latest . --template default`,
  which — as of this SDK — already ships TypeScript + Expo Router in the
  default template; no need to add either by hand). `expo` package version
  `~57.0.18`.
- Routes live under `src/app/` (the current default template uses a `src/`
  directory, not a top-level `app/`) — see `tsconfig.json`'s `@/*` -> `./src/*`
  path alias.
- Dark is the forced/default color scheme (not system-following): see
  `app.json`'s `userInterfaceStyle: "dark"` and the `ThemeProvider` in
  `src/app/_layout.tsx`.
- `web.output` is `"single"` (SPA build) to match Caddy's `try_files {path}
  /index.html` fallback (`infra/caddy/Caddyfile`).

## Structure

```
src/app/_layout.tsx          root layout (dark theme, no header)
src/app/(tabs)/_layout.tsx   tab navigator (Chat, Files)
src/app/(tabs)/chat.tsx      placeholder — "Chat — coming M2-06"
src/app/(tabs)/files.tsx     placeholder — "Files — coming M3-05"
lib/api.ts                   apiBase() / wsUrl() / apiFetch<T>() / ApiError — the only place URLs are built
lib/chatSocket.ts             typed WS client for /ws/chat/{thread_id} (M2-06 imports its frame types)
lib/__tests__/               Jest (jest-expo) unit tests for the above
```

## Local development

```bash
npm install
npx expo start          # dev server; press w for web, or scan the QR code with Expo Go
npm test                # jest (jest-expo preset)
npx tsc --noEmit        # typecheck
npx expo lint           # eslint (eslint-config-expo)
npx expo export --platform web   # production web build -> dist/
```

Native builds read the API/WS host from `EXPO_PUBLIC_API_HOST` (defaults to
`http://homeai.local`, see `.env.example` at the repo root). The web build is
always same-origin — Caddy serves the exported SPA and proxies `/api/*` and
`/ws/*` to `agent-server`.

## Docker / production build

`infra/caddy/Dockerfile`'s `frontend-build` stage runs `npm ci` + `npx expo
export --platform web` against this directory and copies the resulting
`dist/` output into the final `caddy:2-alpine` image at `/srv/www`.
