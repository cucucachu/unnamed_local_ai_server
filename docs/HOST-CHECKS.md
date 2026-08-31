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
- [ ] (M2-06) Phone browser: send a message, watch tokens stream live.
- [ ] (M2-06) Expo Go: same, confirming keyboard behavior and send button.
- [ ] (M2-07) Phone browser at `http://homeai.local`: send "create a file
      called from-my-phone.txt containing hi" — tool card appears in chat —
      then verify on the host the file exists. **(GATE G1+G2)**
- [ ] (M2-07) Tokens visibly stream (not one blob at the end). **(GATE G1+G2)**

> PM sign-off for GATE G1+G2: once the two `(M2-07)` items above are verified
> on the real host, record it here as `G1+G2 passed <date>` — intentionally
> left as a placeholder, not filled in by the automated `M2-07` run, since a
> real sign-off needs a human date/confirmation, not a script's guess.

## M3

## M4

## M5

## M6
