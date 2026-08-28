# M6-02 — Expo Go native parity (config + checklist)

**Milestone**: M6 · **Size**: S · **Depends on**: M2-05 (and benefits from all frontend tickets) · **Blocks**: M6-03

## Context

Web has been the continuously-verified target; native runs via Expo Go against the same LAN
APIs (PLAN.md "Native app distribution is a fast-follow, dev usage is immediate"). This ticket
makes native usage a documented, configured, one-command affair and sweeps for platform breaks.

## Spec

1. **Config**: `services/frontend/.env.example` with
   `EXPO_PUBLIC_API_HOST=http://homeai.local` (+ comment: use the host's LAN IP if Android
   mDNS fails). Ensure `.env` is gitignored, and `lib/api.ts` reads it (already specced —
   verify).
2. **`services/frontend/README.md`** section "Run on your phone": install Expo Go,
   `npm run start` (ensure script exists = `expo start`), scan QR, requirement that phone and
   host share the LAN; troubleshooting (firewall note: `ufw` must allow the Metro port from
   LAN during dev — add `infra/host/dev-metro-ufw.sh` that opens/closes 8081 with an
   `open|close` arg).
3. **Static parity sweep** (code, not phone): grep the frontend for web-only APIs outside
   `.web.tsx` files / `Platform.OS === "web"` guards — `window.`, `document.`, `localStorage`,
   `navigator.` — fix by guarding or moving to platform files. Add an npm script
   `check:platform` running that grep (simple node script `scripts/check-platform.mjs` in the
   frontend), wired into `npm test`.
4. Confirm `metro`/Expo Go can reach the API: nothing in `lib/api.ts` may assume same-origin
   off-web (review; fix if violated).

## Out of scope

EAS/standalone builds; app icons/splash; push notifications; offline behavior.

## Acceptance criteria (Tier A)

- [ ] `npm test` (now incl. `check:platform`) + `npx tsc --noEmit` green.
- [ ] `npx expo start --port 8081` boots and prints the QR/URL (smoke that config is valid;
      kill after).
- [ ] `dev-metro-ufw.sh open` adds the rule; `close` removes it (check `ufw status`).

## Tier B (append to docs/HOST-CHECKS.md under M6 — PM runs on real phones)

- [ ] Expo Go (iOS and/or Android): chat streams; threads switch; files browse/upload/download;
      video plays with seek; exec card renders. Note any platform break as a new ticket rather
      than fixing ad hoc.
