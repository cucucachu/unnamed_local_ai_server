# Notifications (M20): design

Phone notifications when a chat needs you: an approval card is waiting, or
something new arrived in a chat you weren't watching (a routine run, a
reply that finished in the background). Issues: #294 (platform), #295
(agent-server), #296 (Android app), #297 (settings), #298 (gate).

## 1. Privacy rule

**A notification carries no content.** Google relays it, so it holds only
what's needed to open the right chat:

- `kind`: `needs_approval` or `unread`;
- `thread_id`: the chat's random UUID;
- a fixed title and body: "HomeAI" / "A chat needs you" or "New in a
  chat".

Never a chat title, prompt, reply, tool name, integration or routine name.
Google learns that this phone has HomeAI and when a notification was sent,
nothing else. The app fetches the chat itself, over the LAN or WireGuard,
when it's opened.

## 2. Transport: Firebase Cloud Messaging

Android only wakes a locked phone reliably through FCM (Google Play
services' one connection). The alternatives need either a public push
server (UnifiedPush/ntfy) or the phone keeping its own connection to
HomeAI, which means WireGuard always on and more battery. FCM also reaches
the phone away from home without the VPN: only opening the chat needs it.
UnifiedPush could be added later for phones without Google services.

The household needs a free Firebase project (§9). Without one,
notifications are off and everything else works.

## 3. How a notification happens

```
agent-server: a turn ends with nobody watching
  -> POST platform /internal/notify {user_id, kind, thread_id}   (background, 3 s timeout)
platform: the user's devices -> FCM HTTP v1, through the egress proxy
  -> Google -> the phone shows "A chat needs you"
tap -> the app opens /chat/<thread_id> (signing in first if needed)
```

## 4. Platform (#294)

**`push_devices`**: `token` (FCM registration token, primary key, at most
4096 characters), `user_id`, `session_id` (the session it was registered
under), `platform` (`android`), `created_at`, `last_seen_at`. Registering a
token another user had moves it to the caller (a phone changed hands).

- `POST /api/platform/me/push-devices {token}`: the caller's device, in
  person (a human session, not an agent delegation). Again on every launch
  (updates `last_seen_at`) and when FCM rotates the token.
- `DELETE /api/platform/me/push-devices/{token}`: on sign-out, before the
  session ends.
- `POST /internal/notify {user_id, kind, thread_id}`, agent-server's
  service token like its other `/internal` calls: sends to each of that
  user's devices and answers `204` whatever happened (the caller never
  waits on Google). With no credential configured it does nothing and logs
  that once.

**Only devices with a live session get a notification.** The send query
joins `push_devices` to `sessions` and `users` with the same test as
signing in: not revoked, not expired, user not disabled. A session ends in
many places (sign-out, Settings → Sessions, a password change, disabling a
user, removing a WireGuard device, re-pairing a device, the 30-day expiry)
and the app may drop a session on a `401` without calling `DELETE`, so
that check is what's relied on, not each of those paths. Deleting the
devices is cleanup: every place that sets `sessions.revoked_at`
(`core/sessions.py`, `core/wireguard.py`, `core/device_pairs.py`) deletes
that session's devices in the same transaction, and notify deletes the
devices of any session it finds dead. (Routine grants are sessions too,
but never have devices: registering needs a human session.)

**Sending** (FCM HTTP v1): a JWT signed with the service account's key is
exchanged at `https://oauth2.googleapis.com/token` for an access token,
cached until shortly before it expires. Each device gets one
`POST https://fcm.googleapis.com/v1/projects/<project>/messages:send`:

```json
{"message": {
  "token": "<device>",
  "notification": {"title": "HomeAI", "body": "A chat needs you"},
  "data": {"kind": "needs_approval", "thread_id": "<uuid>"},
  "android": {"collapse_key": "<uuid>",
              "notification": {"channel_id": "chats", "tag": "<uuid>"}}}}
```

The `tag` makes a burst for one chat replace one notification rather than
stack. A `404 UNREGISTERED` (or `400 INVALID_ARGUMENT` on the token)
deletes that device. Other failures are logged without the token. One log
line per notify: user, kind, devices, sent, pruned (never a token).

## 5. Egress

The platform is on `homeai-internal` only (no route out). It sends through
the egress proxy with its own proxy credential, `push:EGRESS_PUSH_TOKEN`
(Basic `Proxy-Authorization`, like the gateway's), which may only `POST`
to `oauth2.googleapis.com:443` and `fcm.googleapis.com:443`, with normal
certificate checks. The proxy keeps this client's `Authorization` header
(it strips it for everyone else). The platform trusts the proxy's CA
(`egress-proxy-ca`, mounted read-only, like web-fetch and the gateway).
`verify_egress.sh` checks: the push credential reaches FCM's host; it
can't POST anywhere else; nobody else can POST to FCM.

## 6. When to notify (#295)

At the end of a turn, where `TurnRunner` records the M17-10 attention
state:

- it ended on an approval with no client attached: `needs_approval`;
- it ended any other way with no client attached (the chat becomes
  unread): `unread`. This covers routine runs, a reply that finished after
  you left, and a run that failed, timed out or was stopped by the
  repeated-call guard (its chat changed);
- a turn someone was watching sends nothing.

"Watching" means a chat socket is attached to the turn (`turn.subscribers`).
The app closes its socket when it goes to the background (§7), so a phone
locked mid-turn counts as gone. A browser tab left open on that chat still
counts as watching.

Two endings send nothing even with no client attached:

- the delegation was refused (`DelegationDenied`): the session behind the
  turn was revoked, so whoever ended it doesn't want this chat on a phone;
- the turn was cancelled by the server shutting down
  (`TurnRunner.shutdown`): otherwise every restart would notify for each
  running turn.

The call runs in the background with a short timeout: it never delays or
fails the turn. It's skipped if the user turned that kind off (§8).

MCP elicitation (#344) would be a third "needs you" state; it would reuse
`needs_approval` or add a kind when it lands.

## 7. The Android app (#296)

- `expo-notifications` with FCM. `google-services.json` (§9) is copied
  into the build by `scripts/build_host_app_android.sh` when present; without
  it the APK builds with notifications off. It should stay out of git:
  #296 adds it to `services/frontend/.gitignore`.
- One channel, "Chats", for both kinds.
- Android 13+ needs `POST_NOTIFICATIONS`. The app asks only from Settings
  → Notifications and when saving a routine in the Routines app, never on
  first launch. A routine the agent creates in chat doesn't prompt; while
  permission isn't granted, its tool result in chat shows a one-line hint
  linking to Settings → Notifications.
- `useChat` closes the chat socket when the app goes to the background and
  reconnects when it's back (`reconnectNow`, which already catches up on a
  turn that ran meanwhile). The turn keeps running detached (M17-01); the
  server then sees nobody watching (§6).
- After sign-in it registers the token, again when it rotates, and
  unregisters on sign-out.
- Tapping a notification opens `/chat/<thread_id>`, after signing in if
  needed. Receiving one refreshes the Chat badge (M17-10).
- In the foreground there's no system notification: on that chat
  nothing, elsewhere the badge updates.
- Web gets nothing for now.

## 8. Settings (#297)

Settings → Notifications: a main switch plus "Needs approval" and "New in
a chat", all on by default. Stored in the user's `SettingsDocument`
(agent-server), which is checked before calling notify. The screen shows
whether Android allows notifications, with a button to ask or to open the
system settings.

## 9. Setting up Firebase (once, by the household's admin)

1. At [console.firebase.google.com](https://console.firebase.google.com),
   create a project (Analytics not needed).
2. Add an Android app with package `ai.homeai.host`; download
   `google-services.json` and put it at
   `services/frontend/google-services.json`. It should be gitignored;
   until #296 adds it to `services/frontend/.gitignore`, don't commit it.
3. Project settings → Service accounts → Generate new private key. Put it
   in `.env` as one line, without printing it:
   `echo "FCM_SERVICE_ACCOUNT_B64=$(base64 -w0 key.json)" >> .env`, then
   delete `key.json`. The project ID is read from it.
4. Rebuild the platform and the APK.

## 10. Testing (#298)

- Platform pytest against a fake FCM over real HTTP (token exchange and
  send): register, move, delete; sign-out and revoke drop devices; notify
  reaches that user's devices only; a device whose session expired, was
  revoked or whose user is disabled gets nothing; unregistered tokens are
  pruned; no credential is a no-op; the payload has no content beyond §1.
- agent-server pytest with the fake platform: a detached turn notifies
  once with the right kind and thread; a watched turn doesn't; a refused
  delegation or a shutdown sends nothing; a setting off sends nothing; the
  platform down doesn't affect the turn.
- Jest: register and unregister, tap routing, foreground suppression,
  badge refresh, the socket closing in the background and reconnecting,
  the permission hint, the settings screen.
- `gate_m20.sh`: stack healthy, those tests, `verify_egress.sh`, and a
  live smoke: a throwaway `e2e-*` user registers a fake token, a routine
  runs with nobody connected, and the platform's notify log line shows one
  `unread` for that user and none for anyone else; revoking the session
  removes the device. (The live platform keeps Google's real endpoints, so
  the smoke reads the platform's record rather than a fake FCM.)
- Tier B on the phone (docs/HOST-CHECKS.md § M20): locked and away on
  WireGuard, a finished routine notifies, the notification shows no chat
  text, tapping opens the chat; "New in a chat" off stops those but not
  approvals.
