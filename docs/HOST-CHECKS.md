# Host checks (Tier B)

Manual, human/host-only verification steps that need the real machine, a phone,
or a LAN device — things a CI script can't check. Each ticket that has Tier B
items appends them under its milestone's heading below. Check items off as
they're verified on the real host; don't attempt these from an automated
agent run.

## M0

- [x] (M0-03) Phone on WiFi: `http://homeai.local` shows the placeholder page. **(GATE G0)**

## M1

- [x] (M1-03) PM sign-off: read `docs/TOOL_CALLING.md` (verdict: GO) and
      confirm comfort with M2-03 proceeding on native tool-calling before
      that ticket starts.

## M2

- [x] (M2-05) Laptop browser on LAN: `http://homeai.local` shows the two-tab shell.
- [x] (M2-05) Phone with Expo Go: `npx expo start` from `services/frontend/`,
      scan QR — app opens, tabs render (native parity smoke).
- [x] (M2-06) Phone browser: send a message, watch tokens stream live.
- [x] (M2-06) Expo Go: same, confirming keyboard behavior and send button.
- [x] (M2-07) Phone browser at `http://homeai.local`: send "create a file
      called from-my-phone.txt containing hi" — tool card appears in chat —
      then verify on the host the file exists. **(GATE G1+G2)**
- [x] (M2-07) Tokens visibly stream (not one blob at the end). **(GATE G1+G2)**

> **PM sign-off: G1+G2 passed 2026-08-30**

## M3

- [x] (M3-04) Phone browser at `http://homeai.local`: create a new chat,
      switch between threads on the list, delete a thread — then reopen a
      remaining thread and confirm its prior history loads (hydration).
- [x] (M3-04) Expo Go: same create/switch/delete/reopen-history flow,
      confirming swipe-to-delete works on the thread list (native gesture,
      not exercised by the web-only browser smoke test).
- [x] (M3-05) Phone browser + Expo Go: browse the Files tab, upload a photo
      from the phone, download it back, delete it.
- [ ] (M3-06) Phone: create a thread + file via chat, reboot the **whole
      host machine**, confirm thread history and file are intact and chat
      continues. (The one check scripts can't do.)

> **PM sign-off: G3 passed ____**

## M4

- [ ] (M4-05) PM reads the suite output and countersigns the isolation section.

## M5

## M6
