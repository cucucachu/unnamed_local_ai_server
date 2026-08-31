# Host checks (Tier B)

Manual, human/host-only verification steps that need the real machine, a phone,
or a LAN device — things a CI script can't check. Each ticket that has Tier B
items appends them under its milestone's heading below. Check items off as
they're verified on the real host; don't attempt these from an automated
agent run.

## M0

- [ ] (M0-03) Phone on WiFi: `http://homeai.local` shows the placeholder page. **(GATE G0)**

## M1

- [ ] (M1-03) PM sign-off: read `docs/TOOL_CALLING.md` (verdict: GO) and
      confirm comfort with M2-03 proceeding on native tool-calling before
      that ticket starts.

## M2

- [ ] (M2-05) Laptop browser on LAN: `http://homeai.local` shows the two-tab shell.
- [ ] (M2-05) Phone with Expo Go: `npx expo start` from `services/frontend/`,
      scan QR — app opens, tabs render (native parity smoke).

## M3

## M4

## M5

## M6
