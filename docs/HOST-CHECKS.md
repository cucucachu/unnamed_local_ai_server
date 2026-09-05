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
- [x] (M3-06) Phone: create a thread + file via chat, reboot the **whole
      host machine**, confirm thread history and file are intact and chat
      continues. (The one check scripts can't do.)

> **PM sign-off: G3 passed 2026-09-01**

## M4

- [x] (M4-05) PM reads the suite output and countersigns the isolation section.
- [x] (M4-06) Phone browser + Expo Go: exec card renders and expands cleanly at phone width.
- [x] (M4-07) From a phone: ask the agent to batch-process something real in your workspace (e.g. "make thumbnails of the images in test-photos/ using ffmpeg or imagemagick") and verify results in the files screen.

> **PM sign-off: G4 passed 2026-09-01**

## M5

- [ ] (M5-02) Phone browser: play the video, scrub the timeline, audio file plays too.
- [ ] (M5-02) Expo Go: same file plays with expo-video controls; seek works.

> **PM sign-off: G5 passed ____**

## M6

- [ ] (M6-01) Phone browser (iOS + Android if available): `http://homeai.local` loads the app; note Android mDNS result.
- [ ] (M6-01) From a non-LAN network (phone on cellular): the address does not load.
- [ ] (M6-02) Expo Go (iOS and/or Android): chat streams; threads switch; files browse/upload/download; video plays with seek; exec card renders. Note any platform break as a new ticket rather than fixing ad hoc.
- [ ] (M6-03) From a phone, run README.md's "What using it looks like" list end to end: organize a messy folder via chat; batch-rename via script; summarize a text file to a new file; play the result media; download a file. Each works from the couch.

> **PM sign-off: v1 accepted ____**

## M7

- [ ] (M7-01) From a phone on the LAN, `http://homeai.local` still loads and a chat turn completes (proves the `homeai-internal`/`homeai-net` split didn't break anything a real device on the LAN actually uses).
- [ ] (M7-06) Phone browser + Expo Go: ask a question that triggers `web_search` — the search card expands cleanly at phone width (title/hostname/snippet per result, no overflow) and tapping a result opens the system browser. Then ask it to fetch a specific URL (`web_fetch`) — that card's expanded scrollable text block also fits/scrolls cleanly at phone width, and tapping the final URL opens the system browser too.
- [ ] (M7-07) From a phone: ask "what's the weather forecast for <your city> tomorrow?" — answer cites at least one link and the link opens.
- [ ] (M7-07) From a phone: ask the agent to "sign up for a newsletter at <some site>" — it declines / reports it can't; `docker compose logs egress-proxy` shows no successful non-GET.

## M8

- [ ] (M8-03) From a phone: approval card is usable one-handed; leaving the chat and returning while an approval is pending shows the card again.

## M9

- [ ] (M9-01) Expo Go (Android/iOS): prompt "Reply with a markdown table of 3 planets and a python code block printing hello" — the same reply renders with monospace code and a scrollable table at phone width.
- [ ] (M9-02) Phone: the collapsed panel and header read cleanly; expanding on a long research turn scrolls sensibly.
