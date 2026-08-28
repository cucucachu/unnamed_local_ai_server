# M2-05 — Expo app scaffold + API/WS client + web export build

**Milestone**: M2 · **Size**: M · **Depends on**: M0-03, M2-01 · **Blocks**: M2-06, M6-02

## Context

Single Expo codebase for web + iOS + Android (PLAN.md P4-1/P4-2). This ticket delivers the
scaffold, the shared API client, and the production web build wired into the Caddy image —
no real screens yet (M2-06/M3-04/M3-05 add them).

## Spec

1. **Scaffold** in `services/frontend/`: `npx create-expo-app@latest . --template default`
   (TypeScript + Expo Router). Commit `package-lock.json`. Record the Expo SDK version in a
   `README.md` line inside `services/frontend/`.
   - `app.json`: set `web.output: "single"` (SPA — matches Caddy's `try_files` fallback),
     app name `HomeAI`, scheme `homeai`.
   - Strip template demo content: routes reduced to `app/_layout.tsx` and a tabs group
     `app/(tabs)/` with `chat.tsx` and `files.tsx` placeholder screens ("Chat — coming M2-06",
     "Files — coming M3-05"), tab icons via `@expo/vector-icons` (message / folder icons).
     Dark color scheme as default.
2. **`lib/api.ts`** — the only place URLs are built:

```ts
import { Platform } from "react-native";

export function apiBase(): string {
  if (Platform.OS === "web") return "";              // same-origin
  return process.env.EXPO_PUBLIC_API_HOST ?? "http://homeai.local";
}
export function wsUrl(path: string): string {        // path like /ws/chat/{id}
  if (Platform.OS === "web") {
    const { protocol, host } = window.location;
    return `${protocol === "https:" ? "wss:" : "ws:"}//${host}${path}`;
  }
  return apiBase().replace(/^http/, "ws") + path;
}
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> { ... }
// JSON fetch wrapper: throws ApiError {status, detail} on non-2xx using body.detail
```

3. **`lib/chatSocket.ts`** — typed WS client for the CONVENTIONS §6 contract:
   - `openChatSocket(threadId, handlers: { onToken, onToolStart, onToolEnd, onTurnStart,
     onTurnEnd, onError })` → returns `{ send(userMessage: string), close() }`.
   - TypeScript discriminated-union types for all server frames (mirror CONVENTIONS §6 exactly;
     export them — M2-06 imports these).
   - Reconnect with backoff (1s/2s/4s, max 3 attempts) if the socket drops outside a turn;
     surface `onError` if it drops mid-turn.
4. **Web export in the Caddy image**: replace the placeholder `frontend-build` stage body in
   `infra/caddy/Dockerfile` (contract from M0-03) with: `node:22-alpine`, copy
   `services/frontend/`, `npm ci`, `npx expo export --platform web`, output `dist` → `/out`.
5. **Unit tests** (jest + `jest-expo` preset): frame-type parsing (`chatSocket` handler
   dispatch given raw JSON strings, including unknown-type tolerance), `apiFetch` error shaping
   (mock fetch). Add `npm test` script.

## Out of scope

Chat UI (M2-06), files UI (M3-05), media (M5-02), EAS builds, auth.

## Acceptance criteria (Tier A)

- [ ] `npm test` green; `npx tsc --noEmit` green.
- [ ] `npx expo export --platform web` succeeds locally producing `dist/index.html`.
- [ ] `docker compose build caddy && docker compose up -d caddy` → `curl -s http://localhost/`
      returns the Expo app HTML (placeholder page gone); tab shell renders (check for the
      bundle `<script>` tag; visual check via browser is Tier B-lite but on-host is fine with
      `curl` only).
- [ ] `curl -s http://localhost/api/health` still proxies (no Caddy regression).

## Tier B (append to docs/HOST-CHECKS.md under M2)

- [ ] Laptop browser on LAN: `http://homeai.local` shows the two-tab shell.
- [ ] Phone with Expo Go: `npx expo start` from `services/frontend/`, scan QR — app opens,
      tabs render (native parity smoke).
