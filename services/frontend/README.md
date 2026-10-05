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
src/app/(tabs)/_layout.tsx   tab navigator (Home, Chat, Files, Settings)
src/app/(tabs)/apps/         Home launcher (M14-02): system tiles + installed instances by space; [instanceId] is the app runner
src/app/(tabs)/chat/         Chat tab (thread list + [threadId])
src/app/(tabs)/files.tsx     Files tab
src/app/(tabs)/settings/     Settings tab stack (M10-07 hub; a tab since M14-02): account, sessions, spaces, users + invites (admins)
src/app/login.tsx            signed-out entry: Setup (bootstrap open) or Login
src/app/invite.tsx           invite accept (/invite?token=…, homeai://invite?token=…)
lib/platform.ts              platform /api/platform/* client (me, sessions, TOTP, spaces, members, admin)
lib/stepUp.ts                withStepUp(): retry once after a password prompt on 403 step_up_required;
                             components/StepUpProvider.tsx owns the prompt for the Settings stack
lib/api.ts                   apiBase() / wsUrl() / apiFetch<T>() / ApiError — the only place URLs are built;
                             attaches credentials and signs out on 401
lib/auth.ts                  platform /api/auth/* client (status, login, setup, invite accept, logout)
lib/session.ts               in-memory session token + authHeaders() + onUnauthorized()
lib/tokenStore.ts            native token persistence (expo-secure-store); tokenStore.web.ts is a no-op
components/AuthProvider.tsx  app-root auth state; AuthGate.tsx holds the app until status answers
lib/chatSocket.ts             typed WS client for /ws/chat/{thread_id} (M2-06 imports its frame types)
components/AppRunner.tsx     runner: sandbox + /ws/platform/events relay + hot reload + error overlay
components/AppSandbox.tsx    native sandbox (react-native-webview); AppSandbox.web.tsx is the iframe
lib/appHost.ts               @homeai/sdk/host glue: bridge bound to one instance, sandbox document
lib/apps.ts                  installed-instance listing; lib/platformEvents.ts the events socket
metro.config.js              watches ../../packages/homeai-sdk (the file: dependency @homeai/sdk)
lib/__tests__/               Jest (jest-expo) unit tests for the above
```

## Sign-in (M10-06)

The app asks the platform (`GET /api/auth/status`) who's signed in before
showing anything. `src/app/_layout.tsx` guards routes with
`Stack.Protected`: the tabs (Home, Chat, Files, Settings) and media exist
only while signed in, `/login` only while signed out, and `/invite` always. While the server still
wants its first admin, `/login` shows Setup (setup code from `docker compose
logs platform` or `docker compose exec platform cat /data/platform/setup-code`),
with a link to sign in instead for accounts made with the recovery CLI.

- **Web**: the `HttpOnly` `homeai_session` cookie. No token ever touches JS.
- **Expo Go**: auth calls send `X-HomeAI-Client: native`; the
  returned `session_token` goes in `expo-secure-store` and every request,
  native upload/download, media load, and the chat WebSocket send
  `Authorization: Bearer …`. Password login (Expo Go cannot use Android
  Keystore).
- **Host app** (dev client / `expo-dev-client`): `X-HomeAI-Client: host`
  and pairing login (hardware-backed P-256; Settings → Remote access →
  Pair a phone). Password remains a LAN fallback. iOS is a stub.
- Any `401` from a non-auth call (or a chat socket the platform confirms
  has lost its session) signs out back to `/login`. Logout is in Settings.

Test accounts: `docker compose exec platform python -m app.cli create-user
<name> --password-stdin` (see `services/platform/app/cli.py`).

## Local development

```bash
npm install
npm run start:go        # Expo Go (password login)
npx expo start          # dev client after a prebuild/EAS install
npm run android:dev     # host-app Android (needs a prebuild or EAS client)
npm test                # scripts/check-platform.mjs (native-parity sweep) + jest (jest-expo preset)
npx tsc --noEmit        # typecheck
npx expo lint           # eslint (eslint-config-expo)
npx expo export --platform web   # production web build -> dist/
```

Debug APK without a host SDK: repo-root `./scripts/build_host_app_android.sh`
(throwaway Docker Android image). Maintainer signed builds: `eas.json`
development profile (do not `eas login` from an agent).

Native builds read the API/WS host from `EXPO_PUBLIC_API_HOST` (a
comma-separated list is probed at startup, on foreground and after a network
failure, and the first that answers wins — e.g.
`http://192.168.0.108,http://10.13.13.1` for LAN then WireGuard; defaults to
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
`dist/` output into the final Caddy image at `/srv/www` (`caddy:2-alpine`
plus an xcaddy-built binary with the DuckDNS plugin).
