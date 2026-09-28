# app-runtime spike (M12-01)

Risk-gate prototype for the sandboxed app runtime in `docs/PLATFORM.md` §7.
Verdicts and numbers: [`docs/spikes/app_runtime.md`](../../docs/spikes/app_runtime.md).
Nothing here touches the compose stack. Everything runs from this directory,
using its own `node_modules`, throwaway `docker run --rm` containers, and
headless Chromium via Playwright. Raw results go to `results/*.json`.

```bash
cd spikes/app_runtime
npm ci                 # esbuild, react 19.2.3, react-native-web 0.21.2, playwright 1.62.1, jsdom, ...
(cd expo-host && npm ci)   # only for exp:expo-web and the Expo Go test
npm run exp:all        # every experiment below, in order
```

| # | Experiment | Command | Needs |
|---|---|---|---|
| 1 | Compile: runtime + app bundle sizes, compile time, import allowlist diagnostics | `npm run exp:compile` | node |
| 2 | Sandboxed iframe (plain host page): render, navigation, RPC latency, hot reload, escape probes with/without CSP, self-navigation, viewer role | `npm run exp:sandbox` | node, Chromium |
| 2b | The same inside a real Expo SDK 57 web export (`expo-host/`) | `npm run exp:expo-web` | + `expo-host` deps |
| 3 | WebView emulation: same HTML as a top-level opaque-origin doc, react-native-webview 13.16.1 transport semantics, host = `expo-host/bridgeHost.ts` | `npm run exp:webview` | node, Chromium |
| 3 | **Expo Go on a phone** (manual) | see [Expo Go](#expo-go-manual-maintainer) | phone |
| 4 | Router: shim size vs react-navigation | `npm run exp:router` | npm registry |
| 5 | Smoke render in `node:22-alpine --network none --read-only` | `npm run exp:smoke` | docker |
| 6 | Schema diffing: sqlite3def matrix + the Python differ, in `python:3.12-slim --network none` | `npm run exp:schema` | docker (+ one download of sqlite3def) |
| 7 | WAL read-only: writer container + `:ro` reader container, 3 phases + WAL-pinning abuse case | `npm run exp:wal` | docker |

## Layout

```text
apps/groceries/          two-screen sample app (app/_layout.tsx, app/index.tsx, app/item/[id].tsx, schema.sql)
apps/bad-imports/        allowlist fixtures (react-dom, fs, node:os, ../ escape)
apps/bad-symlink/        symlink pointing outside the app dir
apps/render-throw/       deliberate render-time throw + SQL typo (smoke-render fixture)
runtime/                 runtime bundle source: index.tsx (module registry, boot, error boundary, hot reload),
                         router.tsx (expo-router shim), sdk.ts (@homeai/sdk + expo-sqlite shim), bridge.ts
build/                   build-runtime.mjs, compile-app.mjs (esbuild + allowlist plugin + route table),
                         sandbox-html.mjs (the one HTML doc for srcdoc / WebView), gen-expo-assets.mjs
host/                    stand-in platform + host web page (server.mjs, host.html, host.js)
expo-host/               minimal Expo SDK 57 host: Sandbox.web.tsx (iframe), Sandbox.native.tsx (WebView)
tests/                   Playwright experiments + probes.mjs (escape attempts run inside the sandbox)
smoke/                   smoke-render.mjs (jsdom + react-dom/client + node:sqlite), run-docker.sh
schema/                  cases.py, sqldef_matrix.py, pydiff.py (the recommended differ), pydiff_matrix.py, run.sh
wal/                     writer.py, reader.py, pin_reader.py, run.sh
results/                 committed outputs of the last run
```

Individual tools:

```bash
node build/compile-app.mjs apps/bad-imports          # JSON diagnostics with file:line:col
node build/build-runtime.mjs [--dev] [--curated]     # dist/runtime*.js + size breakdown
node smoke/smoke-render.mjs apps/render-throw         # on the host (needs dist/runtime.dev.js)
python3 schema/pydiff.py live.sqlite schema.sql [--apply [--allow-destructive]]
node host/server.mjs                                  # http://127.0.0.1:8787 — the host page by hand
```

## Expo Go (manual, maintainer)

The agent that built this had no phone and no Android SDK, so **this step has
not been run**. What it did check: `expo-host` type-checks for both the native
and web variants, `npx expo export -p android` builds the Hermes bundle, and
`expo install --check` reports every dependency at the SDK 57 version.

Prerequisites: a phone with **Expo Go for SDK 57**, on the same LAN as this
machine (if it isn't, `npx expo start --tunnel` works but needs internet).

```bash
cd spikes/app_runtime
npm ci
npm run expo:prepare              # writes expo-host/sandboxAssets.generated.ts (runtime + app, ~472 KB HTML)
cd expo-host
npm ci
npx expo start --lan              # scan the QR code: Android = Expo Go "Scan QR code", iOS = Camera app
```

The screen shows three buttons (**Bench**, **Hot reload**, **Probes**), the
sandboxed app in a blue box, and a JSON report under it. Do these in order;
each line says what should happen:

1. **Load.** Within a few seconds the blue box shows a "Groceries" header and the rows Milk, Eggs, Bread. The report shows `ready_v1`.
2. **Navigate + write.** Tap **Eggs**: the header changes to "Eggs" and gets a "‹ Back" link. Tap **Toggle done**: the status changes to "Done". Tap **‹ Back**: Eggs is struck through. Type `Apples` in the input and tap **Add**: Apples appears. The report's `lastPath` follows along (`/item/2`, then `/`).
3. **Bench.** Tap **Bench** and wait about 5–20 s. The report gains `latencyMs_echo` and `latencyMs_db.getAll` (`n: 200`, `p50`, `p95`, `max`). **Pass: p95 < 25 ms for both.**
4. **Hot reload.** Open any item, then tap **Hot reload**. The button text becomes "Toggle done (v2)", you stay on the same item, and the report gains `hotReloadMs` and `ready_v2`.
5. **Probes.** Tap **Probes**. **Pass:** no external browser opens; `blockedNavigations` lists `https://example.com/pwned-top` (and possibly the `window.open`/form URLs); in `probes`, every `fetch …`, `XMLHttpRequest`, `WebSocket`, and `image beacon` entry reads `blocked (...)`. `document.cookie` and `localStorage` are informational: record what they show.
6. Long-press the JSON report, select all, copy it, and paste it into the PR together with the device model, OS version, and Expo Go version. Do it on Android, and on iOS too if you can.

If step 1 shows a blank box, open the WebView devtools (`webviewDebuggingEnabled`
is on in dev: `chrome://inspect` on Android, Safari → Develop on iOS) and paste
the console errors.
