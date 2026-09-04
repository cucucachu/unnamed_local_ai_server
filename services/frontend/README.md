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
npm test                # scripts/check-platform.mjs (native-parity sweep) + jest (jest-expo preset)
npx tsc --noEmit        # typecheck
npx expo lint           # eslint (eslint-config-expo)
npx expo export --platform web   # production web build -> dist/
```

Native builds read the API/WS host from `EXPO_PUBLIC_API_HOST` (defaults to
`http://homeai.local`, see `.env.example` in this directory — `cp` it to
`.env` and adjust if needed; also documented at the repo root's
`.env.example` as part of the whole project's env contract, but Expo itself
only reads `.env` from this directory, not the repo root). The web build is
always same-origin — Caddy serves the exported SPA and proxies `/api/*` and
`/ws/*` to `agent-server`.

## Run on your phone

Expo Go (App Store / Play Store) is the zero-build-pipeline way to run this
app natively during development — no Xcode/Android Studio, no signing, no
install step beyond the Expo Go app itself. A real standalone `.apk`/`.ipa`
(no Expo Go dependency, custom icon) is a later fast-follow via EAS Build —
see the repo root `README.md`'s "Documented fast-follows"; out of scope here.

1. **Install Expo Go** on your phone (same LAN as the host — mDNS/`.local`
   names never cross subnets, so cellular data won't reach it; see
   `docs/NETWORKING.md`'s "Troubleshooting mDNS" for the Android-specific
   `.local` caveat).
2. `cp .env.example .env` in this directory, and confirm/adjust
   `EXPO_PUBLIC_API_HOST` (default `http://homeai.local` — switch to the
   host's LAN IP, e.g. `http://192.168.1.42`, if that specific phone can't
   resolve `.local` names).
3. **Open the Metro port to the LAN** — Expo Go needs to reach the dev
   server (default port 8081) to load the JS bundle, not just the API. This
   is dev-only and separate from the always-on stack's firewall
   (`infra/host/setup-ufw.sh` never opens 8081), so open/close it per
   session:
   ```bash
   sudo infra/host/dev-metro-ufw.sh open    # before starting a session
   sudo infra/host/dev-metro-ufw.sh close   # when done (optional — harmless to leave open)
   ```
4. `npm run start` (= `expo start`) from this directory, then scan the
   printed QR code with Expo Go (Android: in-app scanner; iOS: system Camera
   app first, which hands off into Expo Go).

Troubleshooting: if the app loads but every API call fails, double check
step 2's `EXPO_PUBLIC_API_HOST` — Expo Go has no "same origin" to fall back
on the way the web build does, so a wrong or unreachable host there is the
most common native-only failure mode. `check:platform` (below) also guards
against a whole other class of native-only failure — a web-only global
(`window`, `document`, ...) sneaking into shared code and crashing at
runtime the moment Expo Go's JS engine hits that line.

## Docker / production build

`infra/caddy/Dockerfile`'s `frontend-build` stage runs `npm ci` + `npx expo
export --platform web` against this directory and copies the resulting
`dist/` output into the final `caddy:2-alpine` image at `/srv/www`.
