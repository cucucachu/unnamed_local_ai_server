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
- [x] (M4-07) From a phone: ask the agent to batch-process something real in your files (e.g. "make thumbnails of the images in test-photos/ using ffmpeg or imagemagick") and verify results in the files screen.

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
- [ ] (M8-08) From a phone (Expo Go and browser): stop a long answer; approve then reject a file write; edit an earlier message in both modes and switch branches. **(GATE G8)**
- [ ] (M8-08) Toggle HITL off in Settings; a file write proceeds without a card. **(GATE G8)**

## M9

- [ ] (M9-01) Expo Go (Android/iOS): prompt "Reply with a markdown table of 3 planets and a python code block printing hello" — the same reply renders with monospace code and a scrollable table at phone width.
- [ ] (M9-02) Phone: the collapsed panel and header read cleanly; expanding on a long research turn scrolls sensibly.
- [ ] (M9-03) Expo Go: tapping a file link switches to the Files tab at the right folder with the file highlighted.
- [ ] (M9-04) Android phone, Expo Go: tap the composer -> keyboard opens, composer sits directly above it, last message still visible.
- [ ] (M9-04) Android Chrome at `homeai.local`: same behavior in the browser.
- [ ] (M9-04) iOS Expo Go: no regression.
- [ ] (M9-05) Android + iOS phone: install the CA per NETWORKING.md; `https://homeai.local` loads with no warning; a chat turn completes over `wss://`.
- [ ] (M9-05) `http://homeai.local` still works on a device without the CA.
- [ ] (M9-06) Android Chrome over `https://homeai.local`: tap mic, speak, text appears, edit, send.
- [ ] (M9-06) Expo Go Android: keyboard mic dictates into the composer. Native has no in-app mic button — the OS keyboard's built-in dictation (Gboard / iOS keyboard mic) is the supported path.
- [ ] (M9-07) Android phone (Chrome, https): keyboard behaves; dictate a request that makes the agent research something and write a file; watch the spinner/status; expand "Worked for"; tap the file link; the file opens in Files.
- [ ] (M9-07) Same scenario in Expo Go (keyboard dictation instead of the mic button).
- [ ] (M9-07) iOS Safari/Expo Go: markdown and panel render; no regressions.

> **PM sign-off: G9 passed ____**

## M10

- [x] (M10-08) Get the setup code (`docker compose exec platform cat /data/platform/setup-code`, or `docker compose logs platform | grep -i setup`), open `http://homeai.local`, complete Setup as yourself — you become the bootstrap admin; your pre-Stage-3 chat threads (and settings) appear under your account.
- [x] (M10-08) Settings → Account: enable TOTP with an authenticator app; sign out and back in with the code.
- [x] (M10-08) Settings → Admin → Invites: invite a second person; they accept on their phone in the browser **and** in Expo Go (sign in with the same account); each sees only their own threads.
- [x] (M10-08) Settings → Spaces: create a shared space and add them; they see it in their Spaces list.
- [x] (M10-08) Settings → Sessions: revoke one of your other devices; it is signed out on its next request.

> **PM sign-off: G10 passed 2026-09-28**

## M11

- [x] (M11-05) `sudo scripts/verify_network.sh` on the host: all checks green (`gate_m11.sh` skips it when not run as root).
- [x] (M11-05) From two phones signed in as two different people (you and an invited member): each uploads a file to Personal; neither sees the other's file in Files, and asking your own agent to read the other's file finds nothing. **(GATE G11)**
- [x] (M11-05) Settings → Spaces: add the second person to a shared space as a **viewer**. From your phone, upload a file there (or ask your agent to write one); it shows up on both phones. **(GATE G11)**
- [x] (M11-05) On the viewer's phone: the space is marked read-only, with no Upload/New folder and only Download on a file; asking their agent to write or edit a file in the space is refused and nothing appears on your phone. **(GATE G11)**

- [x] (#194) When you complete Setup (M10-08): the three pre-Stage-3 threads appear under your account, each opens with its full history, and one of them takes a new turn. The hand-over now runs through the `agent_rls_bypass` role, which the automated checks can't reach without completing bootstrap.

> **PM sign-off: G11 passed 2026-09-28**

## M12

The app runner on a real phone (M12-06). The web runner is covered by
`scripts/e2e/app_runner_browser_smoke.sh` and the WebView path by jest
with a mocked WebView (`components/__tests__/AppSandbox.test.tsx`); nothing
has run it inside Expo Go yet. Setup, on the host (stack up with Caddy
built from `main`, the builder image built):

1. Install the fixture app as yourself:
   `node scripts/e2e/app_fixture.mjs install --user <you>` (password
   prompted; `--space <slug>` to install in a shared space you own or edit;
   `--base http://<host>` if `homeai.local` doesn't resolve on the host).
2. `cd services/frontend && npm ci && npx expo start`, with
   `EXPO_PUBLIC_API_HOST` pointing at the host as for the other Expo Go
   checks; open it in Expo Go (SDK 57) and sign in.

- [x] (M12-06) Expo Go (Android and iOS): the **Apps** tab lists "Runtime check" under Personal; tapping it opens the app (build label `v1`, your space and `(owner)`) with no blank screen or red box.
- [x] (M12-06) Expo Go: type an item, tap **Add** — the status reads `added N` and the list shows it; leave the runner, reopen it (and fully restart Expo Go once) — the item is still there.
- [x] (M12-06) Expo Go: with the runner open, run `node scripts/e2e/app_fixture.mjs v2 --user <you>` on the host — within a few seconds the label changes to `v2` and a **Crash** button appears without leaving the screen (hot reload).
- [x] (M12-06) Expo Go: tap **Crash** — the host's "This app hit an error" overlay shows `e2e runner crash`; **Reload** brings the app back (`v2`, items intact). Then `node scripts/e2e/app_fixture.mjs v1 --user <you>` to restore.
- [x] (M12-06) Expo Go: tap an item, then **Back** — in-app navigation works and no external browser or other app opens at any point.
- [x] (M12-06) Expo Go, as a **viewer** of a shared space where the app is installed (`install --space <slug>` as its owner): the row says "View only", the runner shows the read-only note, and **Add** reads `refused: read_only`.
- [x] (M12-06) Clean up: `node scripts/e2e/app_fixture.mjs uninstall --user <you>` (and `--space <slug>`); the Apps tab no longer lists it.


**GATE G12 (M12-08)**: the reference Grocery list app in a shared space on
a phone, live with the browser, plus the M12-01 spike's on-device checks
(`spikes/app_runtime/README.md` → "Expo Go") re-run against the real
runtime and host. `scripts/e2e/gate_m12.sh` covers the web side
(`grocery_app_smoke.sh`: two users' browsers, live `db_changed`) and the
sandbox's credentials (`verify_tenancy.sh` check 20, which runs the same
probe script as below in Chromium). Setup:

1. Finish G10 first (M10 above): you're signed in as yourself, and a shared
   space exists with a second person in it (Settings → Spaces). Note its
   slug.
2. Install the app in the shared space and in your Personal space (each
   prompts for your password):
   `node scripts/e2e/app_fixture.mjs install --app examples/apps/grocery-list --user <you> --space <slug>`
   and `node scripts/e2e/app_fixture.mjs install --app examples/apps/grocery-list --user <you>`.
   For the spike checks, also the fixture app:
   `node scripts/e2e/app_fixture.mjs install --user <you>`.
3. Start Expo Go as in the M12-06 setup (`npx expo start` in
   `services/frontend`, SDK 57) and sign in on the phone. On the laptop,
   open `http://homeai.local` in a browser and sign in (as yourself, or as
   the second person in a private window).

- [x] (M12-08) Expo Go: **Apps** lists "Grocery list" under the shared space and under Personal; opening the shared one renders the list (no blank screen or red box). **(GATE G12)**
- [x] (M12-08) Browser: open the shared space's Grocery list and add `Milk` — within a couple of seconds it appears on the phone without touching it. Tick it on the phone — the browser shows it ticked. **Clear checked** in the browser — it disappears on the phone. **(GATE G12)**
- [x] (M12-08) Phone: add `Eggs`, tap it, set a quantity on the detail screen, **Save** — back on the list it reads `× <quantity>`, and the browser shows the same. **(GATE G12)**
- [x] (M12-08) Phone: open Grocery list under **Personal** — it's a separate, empty list; nothing from the shared one is in it. **(GATE G12)**
- [x] (M12-08) If the second person is a **viewer** of the space: on their phone the app is "View only", with no add box or **Clear checked**, and their taps change nothing.
- [x] (M12-08, spike 1-2: render + navigate) Expo Go: open "Runtime check" (Personal): it renders `v1`; tap an item — its screen opens; **Back** returns to the list.
- [x] (M12-08, spike 4: hot reload keeps the route) Open an item in "Runtime check", then on the host run `node scripts/e2e/app_fixture.mjs v2 --user <you>` — you stay on the item's screen; tap **Back** — the label reads `v2` and a **Crash** button is there, with no reload. Restore with `... v1 --user <you>`.
- [ ] (M12-08, spike 3 + 5: bench and probes) With "Runtime check" open, attach the WebView devtools (`webviewDebuggingEnabled` is on in dev; Android: USB debugging + `chrome://inspect` on the laptop; iOS: Safari → Develop → the phone), pick the sandbox page (`about:blank`), paste the whole of `scripts/e2e/sandbox_probe.js` into its console, then run `await homeaiSandboxProbe('http://<host>')`. **Pass:** `bench.failures` is 0 and `bench.p95` < 25 ms (the spike's bar; it now includes the Wi-Fi round trip to the platform, so record the numbers even if it misses); `escaped` is `[]`; every `fetch …`, `XMLHttpRequest`, `WebSocket …` and `image beacon` entry has `"ok": false`; `document.cookie` / storage are refused or empty. No external browser or other app opens (the `window.open` probe), and the app keeps running.
- [ ] (M12-08, spike 5: navigation) In the same console: `await homeaiSandboxProbe('http://<host>', { bench: 0, navigate: true })` — no browser opens, the app stays on screen and still works (tap **Add**).
- [ ] (M12-08) Paste both JSON reports into issue #149 with the device model, OS version and Expo Go version; Android, and iOS too if you can.
- [x] (M12-08) Clean up: `node scripts/e2e/app_fixture.mjs uninstall --user <you>` (the fixture), and for Grocery list `--app examples/apps/grocery-list` with and without `--space <slug>` (or keep it; it's yours).

> **PM sign-off: G12 passed ____**

## M13

Web G13 is `scripts/e2e/gate_m13.sh` (history, app-tools pytest, the
Ask-the-agent runner smoke, then `g13_grocery_smoke.sh`: the real model
builds a grocery list app from chat, a follow-up adds quantities, the
runner uses it, the agent and the UI edit the same data, revert). Phone
is Tier B. Setup: stack up with Caddy from `main`, builder image built,
signed in as yourself (G10).

- [x] (M13-05) Phone browser or Expo Go: in chat, ask "make me a grocery list app" — the agent creates and builds it; it appears under **Apps** → Personal. **(GATE G13)**
- [x] (M13-05) Follow up "add quantities" — watch the running app hot-reload (or reopen it) with a quantity on each item. **(GATE G13)**
- [x] (M13-05) In the runner, add an item; in **Ask the agent** (or chat), ask what's on the list — the agent answers from the same database. Ask it to add a row; it appears in the open app without a manual reload. **(GATE G13)**
- [x] (M13-05) App info → history: revert the quantities change; the source and the running app go back. **(GATE G13)**

> **PM sign-off: G13 passed 2026-09-28** (run as a health tracker app, since the account already had a grocery list; needed the recursion-limit fix, #225)

## M14

Web G14 is `scripts/e2e/gate_m14.sh` (publish/install smoke, Home launcher,
platform export/system-app/grants pytest, then `g14_exports_smoke.sh`:
family installs a published planner; it reads calendar exports from
personal and family). Phone is Tier B. Setup: stack up with Caddy from
`main`, builder image built, signed in as yourself (G10). Two people, two
phones.

- [x] (M14-06) Two phones: Phone A (author) publishes an app from Personal into the family catalog. Phone B (family editor) opens Catalog, confirms permissions, and installs. Phone A publishes an update; Phone B sees the update badge on Home and approves it. **(GATE G14)**
- [x] (M14-06) Two phones: Calendar is installed in Personal and the family space, each with an event. Family has Planner with calendar `events` reads granted — Planner shows both spaces' events. (A working copy in the family Apps folder auto-grants reads; a pinned Catalog install must send `granted_reads`. The install sheet does not prompt for reads yet.) **(GATE G14)**

> **PM sign-off: G14 passed 2026-10-04** (health tracker published to fam and updated; Calendar ×2 + Planner built by the agent. Fixes found on the way: #227, #234, #235, #236)

## M15

Web G15 is `scripts/e2e/gate_m15.sh` (stack health, WireGuard throwaway-client
smoke, origin-policy through Caddy, Caddy domain/Caddyfile validate, platform
origin / public-HTTPS / WebAuthn / device-pairs pytest, passkey browser smoke
with RP ID restore, then `g15_enrollment_smoke.sh`: public origin refuses
enrollment; temporary `public_https` is always restored). Phone-off-LAN, APK
install, and WAN public HTTPS remain the existing unchecked boxes below.
Setup: stack up with Caddy from `main`, WireGuard kernel module loaded,
signed in as yourself (G10).

- [x] (M15-01) On home Wi-Fi: Settings → Remote access → create a device
      named for this phone → scan the QR in a WireGuard app → the tunnel
      comes up and `http://10.13.13.1` loads the UI, and other apps
      still reach the internet (the profile sets no `DNS =`). Revoke the device; the tunnel no longer reaches the box.
- [x] (M15-01) Off the LAN: router forwards UDP 51820 to the host,
      `WIREGUARD_ENDPOINT` is a dynamic DNS name; phone on cellular with
      the tunnel on loads `http://10.13.13.1`, signs in, and chats.
      Passed 2026-10-04 (TP-Link DDNS, Android browser).
- [ ] (M15-03) Optional: if you already have a DuckDNS name and token,
      split-DNS `HOMEAI_DOMAIN` to this host's LAN IPv4
      (`docs/NETWORKING.md`) and open `https://$HOMEAI_DOMAIN` on the LAN.
      Do not forward TCP 80/443 unless you later opt into public HTTPS.
      Not required to close the ticket (Tier A is
      `scripts/verify_caddy_domain.sh`; default `HOMEAI_DOMAIN` stays
      empty so live `https://homeai.local` is not disturbed).
- [ ] (M15-05, G15 later) Public HTTPS: domain mode on, Settings → Remote
      access → **Allow public HTTPS** (stepped-up admin, LAN/VPN). From a
      non-LAN network, password login is refused (`passkey_required`);
      enrollment/admin still `public_origin`. Do not require agents to
      punch WAN 443 for Tier A. Human WAN steps:
      `infra/host/setup-public-https.md`.
- [x] (M15-06) Install the release APK from
      `HOMEAI_APK_VARIANT=release
      EXPO_PUBLIC_API_HOST=http://<host LAN IP>,http://10.13.13.1
      scripts/build_host_app_android.sh` (Docker; no host SDK. The app uses
      the first address that answers: home Wi-Fi without the VPN, else the
      tunnel. JS is embedded,
      so it works off the LAN over WireGuard; a debug APK needs Metro; the
      release build is also copied to every user's Files → Personal as
      `HomeAI.apk`, via `scripts/publish_host_apk.sh`), or an EAS development client
      the maintainer built — `eas.json` development profile; this host
      has no Android SDK). On the LAN: sign in on the web app → Settings
      → Remote access → **Show pairing QR** → scan it with the phone
      camera (opens the app) or **Scan pairing QR** in the app; pasting
      the code also works. Confirm
      **Sign in with this device** after a biometric unlock. Revoke the
      pair; the phone can no longer sign in that way. Expo Go on the
      same account still uses password (`X-HomeAI-Client: native`). iOS
      is docs-only; no ipa.
      Passed 2026-10-04 (Android release APK; pair, device sign-in with
      TOTP, revoke. Fixes on the way: #241, #242, #245).

> **PM sign-off: G15 passed 2026-10-04** (remote access over WireGuard, on and off the LAN, plus the Android host app with device pairing. Deferred: M15-03 domain and M15-05 public HTTPS, since VPN covers current needs; a real domain needs a non-DuckDNS DNS-01 provider. Fixes found on the way: #239, #240, #241, #242, #245)


## M17

Web G17 is `scripts/e2e/gate_m17.sh` (stack health, the agent-server and
platform routine pytest, then `g17_routines_smoke.sh`: a routine due in a
minute runs with nobody connected and is listed under the routine and in
the chats list, unread at the top until read; disabling stops the next
run; revoking its grant stops it; the Routines system app is listed). Setup: stack up from `main` with the routines scheduler on (the
default), the app updated, signed in as yourself.

- [ ] (M17-01, #228) Start a long reply on the phone, lock it or switch
      apps for a minute, come back: the reply finished (or is still
      streaming) instead of stopping.
- [ ] (M17-07) In a chat, ask for a daily routine, e.g. "every morning at
      7, give me a short briefing: the weather here and anything in my
      notes from yesterday". The approval card shows the schedule in
      words and the prompt; approve it. The Routines app (its tile on
      Home) lists it; its page shows the schedule, the prompt, Edit,
      Delete and its runs.
- [ ] (M17-04, M17-10) The next morning the run's chat is at the top of
      the chats list marked **New**, the Chat tab has a badge, and the
      answer is sensible. Opening it clears the marker and the badge.
- [ ] (M17-05) A routine in **ask** mode that writes a file (e.g. "Run
      now" on one that saves a note to `/personal`) waits for approval:
      its chat is first in the list marked **Needs approval**; approving
      from the phone finishes it and the file is there.
- [ ] (M17-09) A routine with more than 5 runs shows the last 5 and
      **More…**, which lists them all; tapping a run opens its chat.

## M19

Web G19 is `scripts/e2e/gate_m19.sh` (stack health, frontend jest, the
chats-list pytest, then `g19_shell_smoke.sh`: a cold launch lands on the
Chat page in an empty chat; the history drawer lists five chats, waiting
on you, then unread, then the rest, with More, and its New chat button
makes a new chat; swipes go Chat, Personal, a shared space, each with the
right tiles; Add app opens that space's catalog; Files, Routines and
Settings open from their tiles; then the launcher, runner and chat
smokes). Setup: stack up from `main`, the release APK rebuilt from `main`
and installed, signed in as yourself, a member of at least one shared
space with an app in it.

- [ ] (M19-01, M19-07) There's no tab bar: a bar at the bottom shows a
      chat icon (with the badge) and a dot per space. Files, Routines and
      Settings open from their tiles on Personal, and Android's back from
      each returns to the same page.
- [ ] (M19-02, M19-07, M19-08) A cold launch opens an empty chat.
      Dragging right anywhere on the chat (the very edge is still
      Android's back gesture) opens the history every time, as easily as
      swiping between pages; so does the menu button. Tapping the dimmed
      chat beside it, swiping back, or Android's back closes it. It shows
      five chats and **More** for the rest; search,
      the routine-runs filter, and rename and delete on long press work;
      **New chat** at the bottom of the drawer (easy to reach with a thumb,
      stays put while the list scrolls) starts a new chat.
- [ ] (M19-03, M19-07) In an empty chat the big mic in the middle starts
      voice input in one tap (the first time, Android asks for the
      microphone); your words land in the message box; it goes away once
      you've sent something or the keyboard is up. The small mic by the
      message box works too.
- [ ] (M19-04) A space page is an icon grid; an app with an update has a
      dot. Long press: Update, Rebuild (an app you made), History and
      publishing, and Uninstall all work.
- [ ] (M19-05, M19-07) Dragging left from Chat goes to Personal, then to
      each shared space, and back the other way; the drawer drag and the
      page drag don't fight, and neither fights Android's back gesture.
      The dots and title follow. Personal shows Files, Routines, Settings,
      your apps, then **+** last; a shared space shows Files, its apps and
      **+** (no + on a space you only view). Files from a shared page
      opens in that space. **+** lists only what you can add to that
      space. Opening an app and coming back keeps the page.
- [ ] (M19-06) A day of normal use: nothing from the old Home, Files tab
      or Settings tab is missing.
