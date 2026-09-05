// M2-06/M3-04 full-stack chat smoke test — invoked by `chat_browser_smoke.sh`,
// not run directly (that script sets up `node_modules`/browsers first).
// Opens a real headless Chromium against the live stack, drives the actual
// Chat UI (no mocking — real WebSocket, real REST thread endpoints, real
// agent-server, real model), and asserts the full M3-04 thread-list +
// history-hydration flow works end-to-end:
//
//   1. Create a new chat from the UI ("New chat" header button).
//   2. Send a message.
//   3. Go back to the thread list — the title now reflects the message
//      (server-side `_derive_title`, M3-02: first 60 chars, single-line,
//      "..."-truncated).
//   4. Reopen the thread — prior messages render (history hydration, the
//      actual point of M3-04's `GET /api/threads/{id}/messages` call).
//   5. Send a follow-up message and get a real response.
//
// Mirrors the spirit of `scripts/ws_smoke.py` (same "Say exactly: PONG"-style
// prompt, same tolerance for the real model not saying exactly that) but at
// the browser/UI layer instead of raw WebSocket frames.
//
// M6-03: this script's own "New chat" button creates a REAL UUID thread via
// `POST /api/threads` (through the UI), and step 6's `EXEC_MESSAGE` creates
// a real code-exec-manager session for it — neither was ever cleaned up
// before this change (confirmed live on this host: `GET /api/threads`
// showed 7+ leftover "Say exactly: PONG..." threads from prior manual runs
// of this exact script). `main()` now captures the thread id from the URL
// right after creating it and deletes both the thread and its exec session
// in a `finally` block, so re-running this script (standalone or inside
// `gate_full.sh`) doesn't accumulate cruft.
//
// M8-03: after the existing steps (which need HITL *off* so `execute_code`
// in step 6 still runs without an approval card), this script toggles
// `hitl_enabled` via `PUT /api/settings` and drives the three Playwright
// scenarios from that ticket: Approve writes `${WORKSPACE_DIR}/hello.txt`,
// Reject leaves the reject-target file absent, HITL-off writes with no
// approval card. Original settings are restored in `finally`.
//
// M8-04: a fresh three-turn thread, edit turn 2, reload, assert only
// turns 1 + edited 2 remain (`GET /api/threads/{id}/messages` agrees),
// then Regenerate the last answer and assert history length is unchanged.
//
// M9-01: after that, the same thread asks for a markdown table + python
// fence and asserts a real `<table>` and `<pre>`/code node in the bubble.
//
// M8-07: with `thinking_enabled` on, a non-trivial prompt shows the
// "Thinking…" status and the expanded panel contains reasoning text; with
// it off, no reasoning appears and the answer still completes.
//
// M8-05: a fresh three-turn thread, edit turn 2 in Branch mode, assert
// `‹ 2/2 ›`, switch to 1/2 (original continuation), reload keeps it.
//
// M9-03: prompt the model to create a file and mention where it saved it;
// the answer must contain a `file:` link, and clicking it opens the Files
// tab at that path with the entry highlighted. HITL is on (default), so
// the write is Approved like the M8-03 scenarios.
//
// M9-06: a fake `SpeechRecognition` is injected via `addInitScript`. Over
// `https://homeai.local` the composer mic is visible and a transcript
// lands in the draft (never auto-sent). Over `http://` the button is
// absent (`isSecureContext` is false).

import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, rmSync, rmdirSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { chromium } from 'playwright';

const BASE_URL = process.env.CHAT_SMOKE_BASE_URL ?? 'http://localhost/';
const API_BASE =
  process.env.CHAT_SMOKE_API_BASE ?? new URL('/api', BASE_URL).href.replace(/\/$/, '');
const CA_PATH = process.env.CHAT_SMOKE_CA ?? '';
const WORKSPACE_DIR = process.env.WORKSPACE_DIR ?? '';
const TIMEOUT_MS = 120_000;

/** Import Caddy's local CA into a throwaway NSS db so Chromium trusts
 * `https://homeai.local` the same way a phone does after installing
 * `homeai-root-ca.crt`. Playwright has no first-class extra-CA option;
 * Chromium on Linux reads `$HOME/.pki/nssdb`. `PLAYWRIGHT_BROWSERS_PATH`
 * is pinned so changing HOME doesn't hide the downloaded browser. */
function importCaIntoNssHome(caPath) {
  const home = mkdtempSync(path.join(os.tmpdir(), 'homeai-pw-nss-'));
  const nssDir = path.join(home, '.pki', 'nssdb');
  mkdirSync(nssDir, { recursive: true });
  execFileSync('certutil', ['-N', '-d', `sql:${nssDir}`, '--empty-password']);
  execFileSync('certutil', [
    '-A',
    '-n',
    'homeai-root',
    '-t',
    'C,,',
    '-d',
    `sql:${nssDir}`,
    '-i',
    caPath,
  ]);
  return home;
}

// Deliberately > 60 chars (and with a run of internal whitespace) so the
// list-title assertion below actually exercises `_derive_title`'s
// whitespace-collapse + 60-char-ellipsis truncation, not just the
// "message became the title" happy path.
const FIRST_MESSAGE =
  'Say exactly: PONG. This message   is intentionally padded well past sixty characters so the thread list title gets truncated with an ellipsis.';
const FOLLOW_UP_MESSAGE = 'Say exactly: PONG again please.';
// Explicit tool-name + verbatim command, matching how `prompts.py`'s system
// prompt describes `execute_code` ("running scripts" / one-liners) — this
// phrasing reliably gets the model to call the tool rather than just
// describing what it would run.
const EXEC_MESSAGE = 'Use execute_code to run: echo HELLO-UI';
// M7-06: same explicit tool-name + verbatim-argument phrasing convention as
// `EXEC_MESSAGE` above, for the same reason (reliably gets the real model
// to actually call the tool rather than just answering from its own
// training data) — this is "the M7-05 research prompt" the ticket refers
// to (M7-05 added `web_search`/`web_fetch` themselves; this script's own
// prior steps had no coverage of either tool at all until this addition).
const WEB_SEARCH_MESSAGE = 'Use web_search to search for: llama.cpp github repository';
// M8-01: reliably produces many small token chunks over several seconds
// (rather than one quick reply), giving the script room to click Stop
// within ~2s of sending and still be mid-stream when it does.
const COUNT_SLOWLY_MESSAGE = 'Count slowly from 1 to 200, one number per line';
const STOP_FOLLOW_UP_MESSAGE = 'Say exactly: PONG once more, after being stopped.';
// M8-03: explicit tool-name phrasing (same convention as EXEC_MESSAGE /
// WEB_SEARCH_MESSAGE) so the real model actually calls `write_file`
// rather than describing the file. The ticket's prompt text is the
// "Create <name> containing hi" clause.
const HITL_APPROVE_MESSAGE = 'Create hello.txt containing hi. Use write_file.';
const HITL_REJECT_MESSAGE = 'Create hello-reject.txt containing hi. Use write_file.';
const HITL_OFF_MESSAGE = 'Create hello-off.txt containing hi. Use write_file.';
const HITL_APPROVE_FILE = 'hello.txt';
const HITL_REJECT_FILE = 'hello-reject.txt';
const HITL_OFF_FILE = 'hello-off.txt';
// M8-04: a fresh three-turn thread so edit/regenerate aren't fighting the
// earlier exec/web/HITL history. "Say exactly:" keeps the real model from
// calling mutating tools (HITL is already off after step 11).
const EDIT_TURN_1 = 'Say exactly: ALPHA';
const EDIT_TURN_2 = 'Say exactly: BRAVO';
const EDIT_TURN_3 = 'Say exactly: CHARLIE';
const EDIT_TURN_2_EDITED = 'Say exactly: BRAVO-EDITED';
// M9-01: explicit "reply with" phrasing so the model emits markdown rather
// than calling a mutating tool (HITL is on by default; this prompt should
// not trip write_file / execute_code).
const MARKDOWN_MESSAGE =
  'Reply with a markdown table of 3 planets and a python code block printing hello';
// M9-02: read_file is not a mutating tool (HITL-safe). The file is written
// into WORKSPACE_DIR just before the step so the model has something real
// to open.
const ACTIVITY_PANEL_FILE = 'activity-panel-smoke.txt';
const ACTIVITY_PANEL_MESSAGE =
  'Use read_file to read activity-panel-smoke.txt, then say exactly: READ-OK';
// M8-07: a non-trivial prompt that reliably produces thought tokens when
// thinking_enabled is on (Gemma 4 + --reasoning-format deepseek).
const THINKING_ON_MESSAGE =
  'What is 17 times 23? Work through the multiplication carefully, then say only the final number.';
const THINKING_OFF_MESSAGE = 'Say exactly: PONG-NO-THINK';
// M8-05: fresh three-turn thread so fork/switch isn't fighting the
// truncated edit thread from step 12.
const FORK_TURN_1 = 'Say exactly: DELTA';
const FORK_TURN_2 = 'Say exactly: ECHO';
const FORK_TURN_3 = 'Say exactly: FOXTROT';
const FORK_TURN_2_EDITED = 'Say exactly: ECHO-FORKED';
const FILE_LINK_MESSAGE =
  'Create notes/link-test.md with one line, then tell me where you saved it';
const FILE_LINK_REL = 'notes/link-test.md';
const HTTPS_VOICE_URL = process.env.CHAT_SMOKE_HTTPS_URL ?? 'https://homeai.local/';
const HTTP_VOICE_URL = process.env.CHAT_SMOKE_HTTP_URL ?? 'http://homeai.local/';
const VOICE_TRANSCRIPT = 'hello world';

/** Mirrors `chat_ws.py`'s `_derive_title` exactly (see that module's
 * docstring) — collapse whitespace runs to single spaces, then truncate to
 * 60 chars with a trailing `...` if truncation happened. */
function deriveExpectedTitle(content) {
  const singleLine = content.split(/\s+/).filter(Boolean).join(' ');
  if (singleLine.length <= 60) return singleLine;
  return `${singleLine.slice(0, 60)}...`;
}

/** Fills the composer and sends `message`, then waits for a NEW assistant
 * bubble (index >= `priorAssistantCount`) with non-empty text to appear.
 * Returns the (possibly still-streaming-cursor-stripped) reply text. */
async function sendMessageAndAwaitReply(page, message, priorAssistantCount) {
  const assistantBubbleLocator = page.locator('[data-testid="chat-item-assistant"]');

  const input = page.getByPlaceholder('Message…');
  await input.waitFor({ state: 'visible', timeout: 15_000 });
  await input.fill(message);
  await page.getByRole('button', { name: 'Send message' }).click();

  const deadline = Date.now() + TIMEOUT_MS;
  let replyText = '';
  let sawNonEmptyText = false;
  while (Date.now() < deadline) {
    const count = await assistantBubbleLocator.count();
    if (count > priorAssistantCount) {
      const newest = assistantBubbleLocator.nth(count - 1);
      const text = (await newest.textContent())?.trim() ?? '';
      if (text.length > 0) {
        sawNonEmptyText = true;
        replyText = text;
        break;
      }
    }
    await page.waitForTimeout(300);
  }

  if (!sawNonEmptyText) {
    throw new Error(`no assistant bubble with text appeared within ${TIMEOUT_MS}ms of sending "${message}"`);
  }
  return replyText.trim();
}

async function expandLastActivityPanel(page) {
  const headers = page.locator('[data-testid="turn-activity-header"]');
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    const count = await headers.count();
    if (count > 0) {
      await headers.nth(count - 1).click();
      return;
    }
    await page.waitForTimeout(200);
  }
  throw new Error('no turn-activity-header to expand');
}

async function waitForWorkedFor(page, timeoutMs = TIMEOUT_MS) {
  const locator = page.locator('[data-testid="turn-activity-duration"]');
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const count = await locator.count();
    if (count > 0) {
      const text = (await locator.last().textContent()) ?? '';
      if (/Worked for /.test(text)) return text.trim();
    }
    await page.waitForTimeout(300);
  }
  throw new Error(`"Worked for" header did not appear within ${timeoutMs}ms`);
}

/** Polls until an element with exact text `text` is visible (used for the
 * thread-list title assertion, and for the "back on the list screen"/"back
 * on the thread screen" navigation waits below). */
async function waitForText(page, text, timeoutMs = 15_000) {
  const locator = page.getByText(text, { exact: true }).first();
  await locator.waitFor({ state: 'visible', timeout: timeoutMs });
  return locator;
}

/** Polls until `locator`'s match count exceeds `priorCount` — used for the
 * exec tool-card appearance check (step 6), mirroring the same poll-based
 * style as `sendMessageAndAwaitReply` above rather than a single `waitFor`
 * (Playwright's built-in count assertions don't expose a plain locator API
 * for "count changed" the way `expect(locator).toHaveCount(n)` does without
 * pulling in the `expect` import this script otherwise avoids). */
async function expectCountAbove(locator, priorCount, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if ((await locator.count()) > priorCount) return;
    await locator.page().waitForTimeout(300);
  }
  throw new Error(`${label} did not appear within ${timeoutMs}ms`);
}

/** Polls `container`'s text content until it matches any of `patterns` —
 * used to wait for the exec card's status chip (success/failure/timeout;
 * see `ToolItemCard`'s exec branch in `chat/[threadId].tsx`) without caring
 * which one actually rendered here (the exit-0 assertion right after this
 * call is what actually pins down the expected happy-path result). */
async function waitForAnyText(container, patterns, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const text = (await container.textContent()) ?? '';
    if (patterns.some((pattern) => pattern.test(text))) return text;
    await container.page().waitForTimeout(300);
  }
  throw new Error(`none of [${patterns.join(', ')}] appeared within ${timeoutMs}ms`);
}

/** M6-03: best-effort REST DELETE of the thread this script's own "New
 * chat" button created, plus the code-exec-manager session `EXEC_MESSAGE`
 * creates for it (session_id == threadId, already exec-manager-safe since
 * a UUID only ever contains `[a-z0-9-]`). Same "curl not installed;
 * urllib/`docker exec` are the house workarounds" conventions as every
 * other `scripts/e2e/*.sh` script — code-exec-manager publishes no host
 * port (M4-03), so its session is reached by execing python3 directly
 * inside its own container against its own localhost:8090. */
/** GET/PUT `/api/settings` via python3 urllib (this host has no `curl`). */
function pythonSslPreamble() {
  // urllib on this host has no curl; when the smoke is pointed at
  // https://homeai.local the Caddy local CA must be loaded explicitly.
  return `
import ssl, urllib.request
def _ssl_ctx():
    ca = ${JSON.stringify(CA_PATH)}
    if not ca:
        return None
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(ca)
    return ctx
def urlopen(req, timeout=15):
    ctx = _ssl_ctx()
    if ctx is None:
        return urllib.request.urlopen(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)
`;
}

function settingsRequest(method, body) {
  const script = `${pythonSslPreamble()}
import json, sys

url, method, raw_body = sys.argv[1], sys.argv[2], sys.argv[3]
req = urllib.request.Request(url, method=method)
if raw_body:
    req.data = raw_body.encode()
    req.add_header('Content-Type', 'application/json')
with urlopen(req, timeout=15) as resp:
    sys.stdout.write(resp.read().decode())
`;
  const raw = execFileSync(
    'python3',
    ['-c', script, `${API_BASE}/settings`, method, body ? JSON.stringify(body) : ''],
    { encoding: 'utf8' },
  );
  return JSON.parse(raw);
}

function workspaceFilePath(name) {
  if (!WORKSPACE_DIR) {
    throw new Error('WORKSPACE_DIR is not set (chat_browser_smoke.sh exports it from .env)');
  }
  return path.join(WORKSPACE_DIR, name);
}

function removeWorkspaceFileBestEffort(name) {
  try {
    rmSync(workspaceFilePath(name), { force: true });
  } catch {
    // best-effort
  }
}

function fetchThreadMessages(threadId) {
  const script = `${pythonSslPreamble()}
import sys
with urlopen(sys.argv[1], timeout=15) as resp:
    sys.stdout.write(resp.read().decode())
`;
  const raw = execFileSync('python3', ['-c', script, `${API_BASE}/threads/${threadId}/messages`], {
    encoding: 'utf8',
  });
  return JSON.parse(raw);
}

function cleanupThreadBestEffort(threadId) {
  if (!threadId) return;
  const deleteThreadScript = `${pythonSslPreamble()}
import sys

req = urllib.request.Request(sys.argv[1], method='DELETE')
try:
    urlopen(req, timeout=15)
except Exception:
    pass
`;
  try {
    execFileSync('python3', ['-c', deleteThreadScript, `${API_BASE}/threads/${threadId}`]);
  } catch {
    // best-effort
  }

  const deleteSessionScript = `
import sys
import urllib.request

try:
    urllib.request.urlopen(urllib.request.Request(f'http://localhost:8090/sessions/{sys.argv[1]}', method='DELETE'), timeout=15)
except Exception:
    pass
`;
  try {
    execFileSync('docker', ['exec', 'homeai-code-exec-manager-1', 'python3', '-c', deleteSessionScript, threadId]);
  } catch {
    // best-effort — e.g. no execute_code call ever happened this run, so no session ever existed
  }
}

/** Injected into the page before any script runs so Chromium's missing
 * (or vendor-backed) SpeechRecognition is replaced with a deterministic
 * interim → final sequence. Must be a self-contained function — Playwright
 * serializes it into the page. */
function installFakeSpeechRecognition() {
  class FakeSpeechRecognition {
    constructor() {
      this.continuous = false;
      this.interimResults = false;
      this.lang = '';
      this.onresult = null;
      this.onerror = null;
      this.onend = null;
    }
    start() {
      const emit = (transcript, isFinal) => {
        const result = [{ transcript, confidence: 1 }];
        result.isFinal = isFinal;
        this.onresult?.({ resultIndex: 0, results: [result] });
      };
      setTimeout(() => {
        emit('hello ', false);
        setTimeout(() => {
          emit('hello world', true);
          this.onend?.();
        }, 40);
      }, 20);
    }
    stop() {
      this.onend?.();
    }
    abort() {
      this.onend?.();
    }
  }
  window.SpeechRecognition = FakeSpeechRecognition;
  window.webkitSpeechRecognition = FakeSpeechRecognition;
}

async function openNewChatOn(page, origin) {
  await page.goto(origin, { waitUntil: 'domcontentloaded' });
  await page.getByRole('tab', { name: 'Chat' }).click();
  const newChatButton = page.locator('[data-testid="new-chat-header-button"]');
  await newChatButton.waitFor({ state: 'visible', timeout: 15_000 });
  await newChatButton.click();
  await page.waitForURL(/\/chat\/[^/]+/, { timeout: 15_000 });
  return new URL(page.url()).pathname.split('/').filter(Boolean).pop();
}

async function assertVoiceInput(browser, httpsContextOptions) {
  const httpsContext = await browser.newContext(httpsContextOptions);
  await httpsContext.addInitScript(installFakeSpeechRecognition);
  const httpsPage = await httpsContext.newPage();
  let httpsThreadId;
  try {
    httpsThreadId = await openNewChatOn(httpsPage, HTTPS_VOICE_URL);
    const mic = httpsPage.locator('[data-testid="chat-mic"]');
    await mic.waitFor({ state: 'visible', timeout: 10_000 });
    await httpsPage.getByPlaceholder('Message…').waitFor({ state: 'visible', timeout: 15_000 });
    await mic.click();
    const input = httpsPage.getByPlaceholder('Message…');
    const deadline = Date.now() + 10_000;
    let value = '';
    while (Date.now() < deadline) {
      value = await input.inputValue();
      if (value.includes(VOICE_TRANSCRIPT)) break;
      await httpsPage.waitForTimeout(100);
    }
    if (!value.includes(VOICE_TRANSCRIPT)) {
      throw new Error(
        `Step 18: expected composer to contain "${VOICE_TRANSCRIPT}" over ${HTTPS_VOICE_URL}, got "${value}"`,
      );
    }
    console.log(`Step 18 OK — https mic visible; transcript landed in composer ("${value}")`);
  } finally {
    await httpsContext.close();
    cleanupThreadBestEffort(httpsThreadId);
  }

  const httpContext = await browser.newContext();
  await httpContext.addInitScript(installFakeSpeechRecognition);
  const httpPage = await httpContext.newPage();
  let httpThreadId;
  try {
    httpThreadId = await openNewChatOn(httpPage, HTTP_VOICE_URL);
    await httpPage.getByPlaceholder('Message…').waitFor({ state: 'visible', timeout: 15_000 });
    const httpMicCount = await httpPage.locator('[data-testid="chat-mic"]').count();
    if (httpMicCount !== 0) {
      throw new Error(
        `Step 18: expected no mic button on ${HTTP_VOICE_URL} (insecure context), found ${httpMicCount}`,
      );
    }
    console.log(`Step 18 OK — http (${HTTP_VOICE_URL}) has no mic button`);
  } finally {
    await httpContext.close();
    cleanupThreadBestEffort(httpThreadId);
  }
}

async function sendAndAwaitApprovalCard(page, message, timeoutMs = TIMEOUT_MS) {
  const input = page.getByPlaceholder('Message…');
  await input.waitFor({ state: 'visible', timeout: 15_000 });
  await input.fill(message);
  await page.getByRole('button', { name: 'Send message' }).click();

  const card = page.locator('[data-testid="approval-card"]');
  await card.waitFor({ state: 'visible', timeout: timeoutMs });
  return card;
}

async function main() {
  const startedAt = Date.now();
  let threadId;
  let editThreadId;
  let forkThreadId;
  let savedSettings = null;
  const launchOptions = { headless: true };
  const contextOptions = {};
  if (CA_PATH && new URL(BASE_URL).protocol === 'https:') {
    try {
      const nssHome = importCaIntoNssHome(CA_PATH);
      const browsersPath =
        process.env.PLAYWRIGHT_BROWSERS_PATH ||
        path.join(os.homedir(), '.cache', 'ms-playwright');
      launchOptions.env = {
        ...process.env,
        HOME: nssHome,
        PLAYWRIGHT_BROWSERS_PATH: browsersPath,
      };
      console.log(`Trusting Caddy CA via NSS db (${CA_PATH})`);
    } catch (err) {
      // certutil (libnss3-tools) is optional. If it isn't installed,
      // Playwright still needs a way to load https://homeai.local — we
      // already required the CA file on the HTTPS path, so this is a
      // scoped fallback, not a blanket ignore.
      console.log(
        `certutil NSS import failed (${err.message}); using Playwright ignoreHTTPSErrors with ${CA_PATH}`,
      );
      contextOptions.ignoreHTTPSErrors = true;
    }
  }
  const browser = await chromium.launch(launchOptions);
  try {
    // Existing steps include `execute_code` (step 6), which HITL-on would
    // pause behind an approval card. Force HITL off for those, then
    // toggle it for the M8-03 scenarios below. Restore in `finally`.
    savedSettings = settingsRequest('GET');
    settingsRequest('PUT', { hitl_enabled: false });

    const context = await browser.newContext(contextOptions);
    await context.addInitScript(installFakeSpeechRecognition);
    const page = await context.newPage();
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });

    // Explicit navigation to the Chat tab (even though it's also the `/`
    // redirect target today — see `src/app/(tabs)/index.tsx` — so this
    // script keeps working if that default ever changes).
    await page.getByRole('tab', { name: 'Chat' }).click();

    // --- Step 1: create a new chat from the UI -------------------------
    const newChatButton = page.locator('[data-testid="new-chat-header-button"]');
    await newChatButton.waitFor({ state: 'visible', timeout: 15_000 });
    await newChatButton.click();
    // M6-03: capture the real thread id expo-router pushes into the URL
    // (`router.push('/chat/[threadId]', ...)` in `chat/index.tsx`) so it —
    // and its exec-manager session — can be cleaned up in `finally`.
    await page.waitForURL(/\/chat\/[^/]+/, { timeout: 15_000 });
    threadId = new URL(page.url()).pathname.split('/').filter(Boolean).pop();

    // --- Step 2: send the first message ---------------------------------
    const firstReply = await sendMessageAndAwaitReply(page, FIRST_MESSAGE, 0);
    console.log(`Step 2 OK — assistant replied: ${firstReply}`);

    // --- Step 3: back to the list — title reflects the message ---------
    await page.goBack();
    const expectedTitle = deriveExpectedTitle(FIRST_MESSAGE);
    const titleRow = await waitForText(page, expectedTitle, 20_000);
    console.log(`Step 3 OK — thread list shows truncated title: "${expectedTitle}"`);

    // --- Step 4: reopen the thread — prior messages render (hydration) -
    await titleRow.click();
    // The FIRST_MESSAGE user bubble and the assistant's `firstReply` both
    // being present again (without re-sending anything) is the actual
    // proof that `GET /api/threads/{id}/messages` hydration worked, rather
    // than just a fresh/empty socket session.
    await waitForText(page, FIRST_MESSAGE, 20_000);
    const assistantBubbleLocator = page.locator('[data-testid="chat-item-assistant"]');
    await assistantBubbleLocator.first().waitFor({ state: 'visible', timeout: 20_000 });
    const hydratedAssistantCount = await assistantBubbleLocator.count();
    if (hydratedAssistantCount < 1) {
      throw new Error('reopened thread shows no hydrated assistant message');
    }
    console.log('Step 4 OK — reopened thread renders prior user + assistant messages (history hydration)');

    // --- Step 5: follow-up message gets a real response ----------------
    const followUpReply = await sendMessageAndAwaitReply(page, FOLLOW_UP_MESSAGE, hydratedAssistantCount);
    console.log(`Step 5 OK — assistant replied to follow-up: ${followUpReply}`);

    // --- Step 6: execute_code tool call renders as an exec card (M4-06) -
    // Requires the real stack (agent-server + model-runner +
    // code-exec-manager) to be up — already true of every prior step here.
    const toolCardLocator = page.locator('[data-testid="chat-item-tool"]');
    const priorToolCardCount = await toolCardLocator.count();

    const input = page.getByPlaceholder('Message…');
    await input.waitFor({ state: 'visible', timeout: 15_000 });
    await input.fill(EXEC_MESSAGE);
    await page.getByRole('button', { name: 'Send message' }).click();

    await page.getByRole('button', { name: 'Send message' }).waitFor({ state: 'visible', timeout: TIMEOUT_MS });
    await expandLastActivityPanel(page);
    await expectCountAbove(toolCardLocator, priorToolCardCount, 15_000, 'exec tool card');
    const toolCard = toolCardLocator.nth(priorToolCardCount);
    console.log('Step 6 OK — exec tool card appeared');

    // Wait for the run to finish (the success/failure/timeout chip only
    // renders once `resultPreview` arrives at `tool_end` — see
    // `lib/execResult.ts` / `ToolItemCard`'s exec branch) before expanding.
    await waitForAnyText(toolCard, [/exit 0/, /exit \d+/, /timed out/], 60_000);

    // Expand the card (same tap-to-toggle header as every other tool card).
    const toolHeader = toolCard.locator('[data-testid="chat-item-tool-header"]');
    await toolHeader.click();

    await waitForText(page, 'HELLO-UI', 15_000);
    console.log('Step 6 OK — expanded exec card shows command output (HELLO-UI)');

    const exitZeroLocator = toolCard.getByText('exit 0');
    await exitZeroLocator.first().waitFor({ state: 'visible', timeout: 5_000 });
    console.log('Step 6 OK — exit-0 chip rendered');

    // --- Step 7: web_search tool call renders as a search card whose
    // expanded view lists >= 1 result with a real https:// link (M7-06) -
    // Requires the real stack (agent-server + model-runner + web-fetch +
    // searxng) to be up — same precondition as step 6 for exec.
    const priorSearchCardCount = await toolCardLocator.count();
    await input.fill(WEB_SEARCH_MESSAGE);
    await page.getByRole('button', { name: 'Send message' }).click();

    await page.getByRole('button', { name: 'Send message' }).waitFor({ state: 'visible', timeout: TIMEOUT_MS });
    await expandLastActivityPanel(page);
    await expectCountAbove(toolCardLocator, priorSearchCardCount, 15_000, 'web_search tool card');
    const searchCard = toolCardLocator.nth(priorSearchCardCount);
    console.log('Step 7 OK — web_search tool card appeared');

    // Wait for the done state — either the "N results" count chip or an
    // "error" chip (see `webSearchChipInfo` in `chat/[threadId].tsx`) —
    // before expanding; fail fast with a clear message if it's the error
    // chip rather than timing out looking for result links that will never
    // appear.
    const searchChipText = await waitForAnyText(searchCard, [/\d+ results?/, /error/], 60_000);
    if (!/\d+ results?/.test(searchChipText)) {
      throw new Error(`Step 7: web_search did not return results (chip text: "${searchChipText}")`);
    }

    const searchHeader = searchCard.locator('[data-testid="chat-item-tool-header"]');
    await searchHeader.click();

    // Each result's title is a `Linking.openURL`-backed tappable element
    // with `accessibilityRole="link"` (see `WebSearchToolDetail`) — RN
    // Web surfaces that as an ARIA `role="link"`, which Playwright's
    // `getByRole('link')` matches directly.
    const resultLinkLocator = searchCard.getByRole('link');
    await resultLinkLocator.first().waitFor({ state: 'visible', timeout: 15_000 });
    if ((await resultLinkLocator.count()) < 1) {
      throw new Error('Step 7: expanded search card has no result links');
    }

    // The collapsed hostname/snippet text never contains the raw url by
    // design (see `WebSearchToolDetail`'s doc) — the actual proof that a
    // real `https://` link is behind a result is to click it and observe
    // where it navigates. `Linking.openURL`'s web implementation
    // (`react-native-web`) does `window.open(url, '_blank', 'noopener')`
    // for a single-arg call, which Playwright observes as a new
    // page/"popup" on the browser context.
    const [popup] = await Promise.all([
      page.context().waitForEvent('page', { timeout: 15_000 }),
      resultLinkLocator.first().click(),
    ]);
    const popupUrl = popup.url();
    await popup.close();
    if (!popupUrl.startsWith('https://')) {
      throw new Error(`Step 7: expected the opened result link to be an https:// URL, got "${popupUrl}"`);
    }
    console.log(`Step 7 OK — expanded search card's first result opens an https:// link (${popupUrl})`);

    // The search turn's assistant reply can still be streaming after the
    // result card is asserted — wait for Send to come back so step 8
    // doesn't click a missing composer button.
    await page.getByRole('button', { name: 'Send message' }).waitFor({ state: 'visible', timeout: TIMEOUT_MS });

    // --- Step 8: Stop a running turn (M8-01) ----------------------------
    // Send a message that streams slowly, click Stop (testID="chat-stop")
    // within ~2s (well before the reply could finish), assert the composer
    // re-enables (Send button reappears) and the bubble shows "Stopped",
    // then confirm a normal follow-up message still completes.
    const priorAssistantCountForStop = await assistantBubbleLocator.count();
    await input.fill(COUNT_SLOWLY_MESSAGE);
    await page.getByRole('button', { name: 'Send message' }).click();

    const stopButton = page.locator('[data-testid="chat-stop"]');
    await stopButton.waitFor({ state: 'visible', timeout: 10_000 });
    await page.waitForTimeout(2_000);
    await stopButton.click();
    console.log('Step 8 OK — clicked Stop within 2s of sending');

    // Composer re-enabled: the Stop button is gone and Send is back.
    await page.getByRole('button', { name: 'Send message' }).waitFor({ state: 'visible', timeout: 15_000 });
    console.log('Step 8 OK — composer re-enabled (Send button reappeared)');

    // The cancelled panel header shows "Stopped after Xs".
    const stoppedCaptionLocator = page.locator('[data-testid="chat-item-stopped-caption"]');
    await stoppedCaptionLocator.first().waitFor({ state: 'visible', timeout: 15_000 });
    console.log('Step 8 OK — cancelled panel shows a "Stopped after" header');

    const postStopAssistantCount = await assistantBubbleLocator.count();

    // A follow-up message on the same socket still completes normally.
    const stopFollowUpReply = await sendMessageAndAwaitReply(page, STOP_FOLLOW_UP_MESSAGE, postStopAssistantCount);
    console.log(`Step 8 OK — follow-up after Stop completed normally: ${stopFollowUpReply}`);

    // --- Step 9: HITL approve (M8-03) -----------------------------------
    settingsRequest('PUT', { hitl_enabled: true });
    removeWorkspaceFileBestEffort(HITL_APPROVE_FILE);

    const approvePriorTools = await toolCardLocator.count();
    await sendAndAwaitApprovalCard(page, HITL_APPROVE_MESSAGE);
    console.log('Step 9 OK — approval card appeared (HITL on, write_file)');

    await page.getByRole('button', { name: 'Approve write_file' }).click();
    await page.getByRole('button', { name: 'Send message' }).waitFor({ state: 'visible', timeout: TIMEOUT_MS });
    await expandLastActivityPanel(page);
    await expectCountAbove(toolCardLocator, approvePriorTools, 15_000, 'approved write_file tool card');
    const approveCard = toolCardLocator.nth(approvePriorTools);
    if ((await approveCard.locator('[data-testid="chat-item-tool-rejected-chip"]').count()) > 0) {
      throw new Error('Step 9: approved write_file rendered a rejected chip');
    }
    const approveFileDeadline = Date.now() + TIMEOUT_MS;
    while (Date.now() < approveFileDeadline && !existsSync(workspaceFilePath(HITL_APPROVE_FILE))) {
      await page.waitForTimeout(300);
    }
    if (!existsSync(workspaceFilePath(HITL_APPROVE_FILE))) {
      throw new Error(`Step 9: ${workspaceFilePath(HITL_APPROVE_FILE)} does not exist after Approve`);
    }
    console.log(`Step 9 OK — Approve wrote ${workspaceFilePath(HITL_APPROVE_FILE)}`);
    await page.getByRole('button', { name: 'Send message' }).waitFor({ state: 'visible', timeout: TIMEOUT_MS });

    // --- Step 10: HITL reject (M8-03) -----------------------------------
    removeWorkspaceFileBestEffort(HITL_REJECT_FILE);
    const rejectPriorAssistantCount = await assistantBubbleLocator.count();
    await sendAndAwaitApprovalCard(page, HITL_REJECT_MESSAGE);
    console.log('Step 10 OK — approval card appeared (reject scenario)');

    await page.getByRole('button', { name: 'Reject write_file' }).click();
    await page.getByRole('button', { name: 'Send message' }).waitFor({ state: 'visible', timeout: TIMEOUT_MS });
    await expandLastActivityPanel(page);
    const rejectedChip = page.locator('[data-testid="chat-item-tool-rejected-chip"]');
    await rejectedChip.first().waitFor({ state: 'visible', timeout: TIMEOUT_MS });
    if (existsSync(workspaceFilePath(HITL_REJECT_FILE))) {
      throw new Error(`Step 10: ${workspaceFilePath(HITL_REJECT_FILE)} exists after Reject`);
    }
    const rejectDeadline = Date.now() + TIMEOUT_MS;
    let rejectAck = '';
    while (Date.now() < rejectDeadline) {
      const count = await assistantBubbleLocator.count();
      if (count > rejectPriorAssistantCount) {
        const newest = assistantBubbleLocator.nth(count - 1);
        const text = (await newest.textContent())?.trim() ?? '';
        if (text.length > 0) {
          rejectAck = text.trim();
          break;
        }
      }
      await page.waitForTimeout(300);
    }
    if (!rejectAck) {
      throw new Error('Step 10: assistant did not acknowledge the rejected write');
    }
    if (existsSync(workspaceFilePath(HITL_REJECT_FILE))) {
      throw new Error(`Step 10: ${workspaceFilePath(HITL_REJECT_FILE)} appeared after the assistant replied`);
    }
    console.log(`Step 10 OK — Reject left file absent; assistant: ${rejectAck}`);
    await page.getByRole('button', { name: 'Send message' }).waitFor({ state: 'visible', timeout: TIMEOUT_MS });

    // --- Step 11: HITL off — no approval card (M8-03) -------------------
    settingsRequest('PUT', { hitl_enabled: false });
    removeWorkspaceFileBestEffort(HITL_OFF_FILE);
    const offPriorTools = await toolCardLocator.count();
    await input.fill(HITL_OFF_MESSAGE);
    await page.getByRole('button', { name: 'Send message' }).click();

    const offDeadline = Date.now() + TIMEOUT_MS;
    let sawApprovalCard = false;
    while (Date.now() < offDeadline) {
      if ((await page.locator('[data-testid="approval-card"]').count()) > 0) {
        sawApprovalCard = true;
        break;
      }
      if ((await page.getByRole('button', { name: 'Send message' }).count()) > 0) {
        break;
      }
      await page.waitForTimeout(300);
    }
    if (sawApprovalCard) {
      throw new Error('Step 11: approval card appeared with HITL off');
    }
    await page.getByRole('button', { name: 'Send message' }).waitFor({ state: 'visible', timeout: TIMEOUT_MS });
    await expandLastActivityPanel(page);
    await expectCountAbove(toolCardLocator, offPriorTools, 15_000, 'HITL-off write_file tool card');
    const offCard = toolCardLocator.nth(offPriorTools);
    await waitForAnyText(offCard, [/write_file/], TIMEOUT_MS);
    const offFileDeadline = Date.now() + 30_000;
    while (Date.now() < offFileDeadline && !existsSync(workspaceFilePath(HITL_OFF_FILE))) {
      await page.waitForTimeout(300);
    }
    if (!existsSync(workspaceFilePath(HITL_OFF_FILE))) {
      throw new Error(`Step 11: ${workspaceFilePath(HITL_OFF_FILE)} does not exist with HITL off`);
    }
    console.log('Step 11 OK — HITL off wrote the file with no approval card');

    // --- Step 12: edit turn 2 + regenerate (M8-04) ----------------------
    // Fresh thread so history is exactly three user/assistant turns.
    await page.getByRole('tab', { name: 'Chat' }).click();
    await newChatButton.waitFor({ state: 'visible', timeout: 15_000 });
    await newChatButton.click();
    await page.waitForURL(/\/chat\/[^/]+/, { timeout: 15_000 });
    editThreadId = new URL(page.url()).pathname.split('/').filter(Boolean).pop();

    await sendMessageAndAwaitReply(page, EDIT_TURN_1, 0);
    const afterTurn1Assistants = await assistantBubbleLocator.count();
    await sendMessageAndAwaitReply(page, EDIT_TURN_2, afterTurn1Assistants);
    const afterTurn2Assistants = await assistantBubbleLocator.count();
    await sendMessageAndAwaitReply(page, EDIT_TURN_3, afterTurn2Assistants);
    console.log('Step 12 OK — three-turn chat created');

    const userRows = page.locator('[data-testid="chat-item-user"]');
    if ((await userRows.count()) < 3) {
      throw new Error(`Step 12: expected 3 user bubbles before edit, got ${await userRows.count()}`);
    }
    await userRows.nth(1).locator('[data-testid="chat-item-user-menu"]').click();
    await page.locator('[data-testid="chat-message-action-edit"]').click();
    const editBanner = page.locator('[data-testid="chat-edit-banner"]');
    await editBanner.waitFor({ state: 'visible', timeout: 10_000 });
    await input.fill(EDIT_TURN_2_EDITED);
    await page.getByRole('button', { name: 'Send message' }).click();
    const editDeadline = Date.now() + TIMEOUT_MS;
    let sawEditedReply = false;
    while (Date.now() < editDeadline) {
      const userCount = await userRows.count();
      const assistantCount = await assistantBubbleLocator.count();
      if (userCount === 2 && assistantCount >= 2) {
        const newest = assistantBubbleLocator.nth(assistantCount - 1);
        const text = (await newest.textContent())?.trim() ?? '';
        if (text.length > 0) {
          sawEditedReply = true;
          break;
        }
      }
      await page.waitForTimeout(300);
    }
    if (!sawEditedReply) {
      throw new Error('Step 12: edited turn 2 did not produce a finished assistant reply');
    }
    console.log('Step 12 OK — edited turn 2; waiting for reload hydration');

    await page.reload({ waitUntil: 'domcontentloaded' });
    await waitForText(page, EDIT_TURN_1, 20_000);
    await waitForText(page, EDIT_TURN_2_EDITED, 20_000);
    if ((await page.getByText(EDIT_TURN_3, { exact: true }).count()) > 0) {
      throw new Error('Step 12: turn 3 text still visible after editing turn 2 and reloading');
    }
    const hydratedUsers = await page.locator('[data-testid="chat-item-user"]').count();
    if (hydratedUsers !== 2) {
      throw new Error(`Step 12: expected 2 user bubbles after reload, got ${hydratedUsers}`);
    }

    const apiMessages = fetchThreadMessages(editThreadId);
    const apiUsers = apiMessages.filter((m) => m.role === 'user');
    const apiAssistants = apiMessages.filter((m) => m.role === 'assistant' && m.content);
    if (apiUsers.length !== 2 || apiUsers[0].content !== EDIT_TURN_1 || apiUsers[1].content !== EDIT_TURN_2_EDITED) {
      throw new Error(`Step 12: GET /messages user rows disagree: ${JSON.stringify(apiUsers)}`);
    }
    if (apiAssistants.length !== 2) {
      throw new Error(`Step 12: GET /messages expected 2 assistant rows with content, got ${apiAssistants.length}`);
    }
    console.log('Step 12 OK — after reload, only turns 1 + edited 2 remain (REST agrees)');

    const historyLenBeforeRegen = apiMessages.length;
    const assistantsBeforeRegen = await page.locator('[data-testid="chat-item-assistant"]').count();
    await page.locator('[data-testid="chat-item-assistant-menu"]').last().click();
    await page.locator('[data-testid="chat-message-action-regenerate"]').click();

    const regenDeadline = Date.now() + TIMEOUT_MS;
    let sawRegenReply = false;
    while (Date.now() < regenDeadline) {
      const count = await page.locator('[data-testid="chat-item-assistant"]').count();
      if (count >= assistantsBeforeRegen) {
        const newest = page.locator('[data-testid="chat-item-assistant"]').nth(count - 1);
        const text = (await newest.textContent())?.trim() ?? '';
        if (text.length > 0) {
          sawRegenReply = true;
          break;
        }
      }
      await page.waitForTimeout(300);
    }
    if (!sawRegenReply) {
      throw new Error('Step 12: Regenerate did not produce a new finished assistant reply');
    }

    const apiAfterRegen = fetchThreadMessages(editThreadId);
    if (apiAfterRegen.length !== historyLenBeforeRegen) {
      throw new Error(
        `Step 12: history length changed after Regenerate (${historyLenBeforeRegen} -> ${apiAfterRegen.length})`,
      );
    }
    console.log('Step 12 OK — Regenerate produced a new answer; history length unchanged');

    // --- Step 13: finished assistant bubble renders markdown (M9-01) ----
    // Stay on the edit thread — composer is already idle after Regenerate,
    // and a second "New chat" hop from this screen is flaky (the list
    // header button is not always mounted after a reload).
    const markdownPrior = await page.locator('[data-testid="chat-item-assistant"]').count();
    const markdownReply = await sendMessageAndAwaitReply(page, MARKDOWN_MESSAGE, markdownPrior);
    const markdownBubble = page.locator('[data-testid="chat-item-assistant-bubble"]').last();
    await markdownBubble.waitFor({ state: 'visible', timeout: 15_000 });
    const tableCount = await markdownBubble.locator('table').count();
    const preCount = await markdownBubble.locator('pre').count();
    const codeCount = await markdownBubble.locator('code').count();
    if (tableCount < 1) {
      throw new Error(`Step 13: expected a <table> in the assistant bubble; reply was: ${markdownReply}`);
    }
    if (preCount < 1 && codeCount < 1) {
      throw new Error(`Step 13: expected a <pre>/code block in the assistant bubble; reply was: ${markdownReply}`);
    }
    console.log(`Step 13 OK — assistant bubble has <table> (${tableCount}) and <pre>/code (${preCount}/${codeCount})`);

    // --- Step 14: turn activity panel (M9-02) ---------------------------
    // HITL is still off from step 11; read_file is not mutating anyway.
    writeFileSync(workspaceFilePath(ACTIVITY_PANEL_FILE), 'activity-panel smoke marker\n', 'utf8');
    const activityPriorAssistants = await page.locator('[data-testid="chat-item-assistant"]').count();
    await input.fill(ACTIVITY_PANEL_MESSAGE);
    await page.getByRole('button', { name: 'Send message' }).click();

    const runningPanel = page.locator('[data-testid="turn-activity-spinner"]');
    await runningPanel.first().waitFor({ state: 'visible', timeout: TIMEOUT_MS });
    const assistantsWhileRunning = await page.locator('[data-testid="chat-item-assistant"]').count();
    if (assistantsWhileRunning > activityPriorAssistants) {
      throw new Error('Step 14: partial answer text was visible while the activity panel was collapsed');
    }
    console.log('Step 14 OK — spinner panel while running; no collapsed-visible answer text');

    await page.getByRole('button', { name: 'Send message' }).waitFor({ state: 'visible', timeout: TIMEOUT_MS });
    const workedFor = await waitForWorkedFor(page);
    const activityAssistants = await page.locator('[data-testid="chat-item-assistant"]').count();
    if (activityAssistants <= activityPriorAssistants) {
      throw new Error('Step 14: expected a single markdown answer after the turn finished');
    }
    const activityAnswer = page.locator('[data-testid="chat-item-assistant-bubble"]').last();
    await activityAnswer.waitFor({ state: 'visible', timeout: 15_000 });
    if ((await activityAnswer.locator('[data-testid="markdown"]').count()) < 1) {
      throw new Error('Step 14: finished answer is not markdown');
    }
    console.log(`Step 14 OK — ${workedFor} + markdown answer`);

    await expandLastActivityPanel(page);
    const activityTools = page.locator('[data-testid="chat-item-tool"]');
    await expectCountAbove(activityTools, 0, 15_000, 'read_file tool card inside activity panel');
    const readCardText = (await activityTools.last().textContent()) ?? '';
    if (!/read_file|activity-panel-smoke/.test(readCardText)) {
      throw new Error(`Step 14: expanded panel did not show a read_file card (text: ${readCardText})`);
    }
    console.log('Step 14 OK — expanding the header reveals the tool card');

    await page.reload({ waitUntil: 'domcontentloaded' });
    await waitForText(page, ACTIVITY_PANEL_MESSAGE, 20_000);
    const hydratedWorked = await waitForWorkedFor(page, 20_000);
    console.log(`Step 14 OK — reload shows the same header from history (${hydratedWorked})`);

    // --- Step 15: reasoning stream + thinking toggle (M8-07) ------------
    // Stay on the edit thread (same composer-already-idle reason as step 13).
    // HITL stays off. thinking_enabled defaults false; flip it for this step.
    settingsRequest('PUT', { thinking_enabled: true, hitl_enabled: false });
    const thinkingPriorAssistants = await page.locator('[data-testid="chat-item-assistant"]').count();
    await input.fill(THINKING_ON_MESSAGE);
    await page.getByRole('button', { name: 'Send message' }).click();

    const thinkingStatus = page.locator('[data-testid="turn-activity-status"]');
    const thinkingDeadline = Date.now() + TIMEOUT_MS;
    let sawThinkingStatus = false;
    while (Date.now() < thinkingDeadline) {
      const count = await thinkingStatus.count();
      if (count > 0) {
        const text = ((await thinkingStatus.last().textContent()) ?? '').trim();
        if (text === 'Thinking…') {
          sawThinkingStatus = true;
          break;
        }
      }
      await page.waitForTimeout(200);
    }
    if (!sawThinkingStatus) {
      throw new Error('Step 15: "Thinking…" status did not appear with thinking_enabled on');
    }
    await expandLastActivityPanel(page);
    const reasoningLocator = page.locator('[data-testid="turn-activity-reasoning"]');
    const reasoningDeadline = Date.now() + TIMEOUT_MS;
    let reasoningText = '';
    while (Date.now() < reasoningDeadline) {
      if ((await reasoningLocator.count()) > 0) {
        reasoningText = ((await reasoningLocator.last().textContent()) ?? '').trim();
        if (reasoningText.length > 0) break;
      }
      await page.waitForTimeout(300);
    }
    if (!reasoningText) {
      throw new Error('Step 15: expanded panel did not contain reasoning text with thinking_enabled on');
    }
    console.log(`Step 15 OK — Thinking… + reasoning text (${reasoningText.slice(0, 80)}…)`);

    await page.getByRole('button', { name: 'Send message' }).waitFor({ state: 'visible', timeout: TIMEOUT_MS });
    const thinkingAssistants = await page.locator('[data-testid="chat-item-assistant"]').count();
    if (thinkingAssistants <= thinkingPriorAssistants) {
      throw new Error('Step 15: thinking-on turn did not produce a finished assistant answer');
    }

    settingsRequest('PUT', { thinking_enabled: false, hitl_enabled: false });
    const offPriorAssistants = await page.locator('[data-testid="chat-item-assistant"]').count();
    const offPriorReasoning = await page.locator('[data-testid="turn-activity-reasoning"]').count();
    const offReply = await sendMessageAndAwaitReply(page, THINKING_OFF_MESSAGE, offPriorAssistants);
    if (!offReply) {
      throw new Error('Step 15: thinking-off turn did not complete with an answer');
    }
    // Expand any new finished panel so a leaked reasoning block would be visible.
    const offHeaders = page.locator('[data-testid="turn-activity-header"]');
    if ((await offHeaders.count()) > 0) {
      await offHeaders.last().click();
    }
    const offReasoning = await page.locator('[data-testid="turn-activity-reasoning"]').count();
    if (offReasoning > offPriorReasoning) {
      throw new Error('Step 15: reasoning text appeared with thinking_enabled off');
    }
    console.log(`Step 15 OK — thinking off: no new reasoning; answer completed: ${offReply}`);

    // --- Step 16: fork edit + branch switch (M8-05) ---------------------
    // Fresh thread so history is exactly three user/assistant turns.
    // HITL stays off; "Say exactly:" avoids mutating tools.
    // Go to the list via URL — clicking the Chat tab from a nested
    // `/chat/[id]` screen is a no-op (already on the Chat tab), so the
    // header "New chat" button never mounts (same flake step 13 avoided).
    await page.goto(new URL('/chat', BASE_URL).href, { waitUntil: 'domcontentloaded' });
    await newChatButton.waitFor({ state: 'visible', timeout: 15_000 });
    await newChatButton.click();
    await page.waitForURL(/\/chat\/[^/]+/, { timeout: 15_000 });
    forkThreadId = new URL(page.url()).pathname.split('/').filter(Boolean).pop();

    await sendMessageAndAwaitReply(page, FORK_TURN_1, 0);
    const forkAfter1 = await page.locator('[data-testid="chat-item-assistant"]').count();
    await sendMessageAndAwaitReply(page, FORK_TURN_2, forkAfter1);
    const forkAfter2 = await page.locator('[data-testid="chat-item-assistant"]').count();
    await sendMessageAndAwaitReply(page, FORK_TURN_3, forkAfter2);
    console.log('Step 16 OK — three-turn chat created for fork');

    const forkUsers = page.locator('[data-testid="chat-item-user"]');
    if ((await forkUsers.count()) < 3) {
      throw new Error(`Step 16: expected 3 user bubbles before fork, got ${await forkUsers.count()}`);
    }
    await forkUsers.nth(1).locator('[data-testid="chat-item-user-menu"]').click();
    await page.locator('[data-testid="chat-message-action-edit"]').click();
    await page.locator('[data-testid="chat-edit-banner"]').waitFor({ state: 'visible', timeout: 10_000 });
    await page.locator('[data-testid="chat-edit-mode-fork"]').click();
    await input.fill(FORK_TURN_2_EDITED);
    await page.getByRole('button', { name: 'Send message' }).click();

    const forkEditDeadline = Date.now() + TIMEOUT_MS;
    let sawForkSwitcher = false;
    while (Date.now() < forkEditDeadline) {
      const switcher = page.locator('[data-testid="chat-branch-switcher"]');
      if ((await switcher.count()) > 0) {
        const label = ((await page.locator('[data-testid="chat-branch-label"]').textContent()) ?? '').trim();
        const whole = ((await switcher.textContent()) ?? '').replace(/\s+/g, ' ').trim();
        if (label === '2/2' || /‹\s*2\/2\s*›/.test(whole)) {
          sawForkSwitcher = true;
          break;
        }
      }
      await page.waitForTimeout(300);
    }
    if (!sawForkSwitcher) {
      throw new Error('Step 16: ‹ 2/2 › did not appear after editing turn 2 in fork mode');
    }
    console.log('Step 16 OK — ‹ 2/2 › after fork edit');

    await page.locator('[data-testid="chat-branch-prev"]').click();
    const switchDeadline = Date.now() + 20_000;
    let sawOriginal = false;
    while (Date.now() < switchDeadline) {
      const label = ((await page.locator('[data-testid="chat-branch-label"]').textContent()) ?? '').trim();
      const hasOriginalTwo = (await page.getByText(FORK_TURN_2, { exact: true }).count()) > 0;
      const hasThree = (await page.getByText(FORK_TURN_3, { exact: true }).count()) > 0;
      if (label === '1/2' && hasOriginalTwo && hasThree) {
        sawOriginal = true;
        break;
      }
      await page.waitForTimeout(300);
    }
    if (!sawOriginal) {
      throw new Error('Step 16: switching to 1/2 did not restore the original continuation');
    }
    console.log('Step 16 OK — ‹ 1/2 › shows original turn 2 + turn 3');

    await page.reload({ waitUntil: 'domcontentloaded' });
    await waitForText(page, FORK_TURN_1, 20_000);
    await waitForText(page, FORK_TURN_2, 20_000);
    await waitForText(page, FORK_TURN_3, 20_000);
    const reloadedLabel = ((await page.locator('[data-testid="chat-branch-label"]').textContent()) ?? '').trim();
    if (reloadedLabel !== '1/2') {
      throw new Error(`Step 16: reload lost the 1/2 selection (label=${reloadedLabel})`);
    }
    if ((await page.getByText(FORK_TURN_2_EDITED, { exact: true }).count()) > 0) {
      throw new Error('Step 16: forked text visible after reload on branch 1/2');
    }
    console.log('Step 16 OK — reload kept ‹ 1/2 › and the original continuation');

    // --- Step 17: file: link opens Files tab at that path (M9-03) -------
    // HITL defaults on and is restored in finally; force it on here so the
    // write shows an approval card (same Approve pattern as step 9).
    settingsRequest('PUT', { hitl_enabled: true, thinking_enabled: false });
    try {
      rmSync(workspaceFilePath(FILE_LINK_REL), { force: true });
    } catch {
      // best-effort
    }

    const fileLinkPriorAssistants = await page.locator('[data-testid="chat-item-assistant"]').count();
    await sendAndAwaitApprovalCard(page, FILE_LINK_MESSAGE);
    console.log('Step 17 OK — approval card appeared (HITL on, create notes/link-test.md)');

    await page.getByRole('button', { name: /Approve / }).click();
    const extraApproveDeadline = Date.now() + 20_000;
    while (Date.now() < extraApproveDeadline) {
      if ((await page.locator('[data-testid="approval-card"]').count()) > 0) {
        await page.getByRole('button', { name: /Approve / }).click();
        await page.waitForTimeout(400);
        continue;
      }
      break;
    }

    await page.getByRole('button', { name: 'Send message' }).waitFor({ state: 'visible', timeout: TIMEOUT_MS });
    const fileDeadline = Date.now() + TIMEOUT_MS;
    while (Date.now() < fileDeadline && !existsSync(workspaceFilePath(FILE_LINK_REL))) {
      await page.waitForTimeout(300);
    }
    if (!existsSync(workspaceFilePath(FILE_LINK_REL))) {
      throw new Error(`Step 17: ${workspaceFilePath(FILE_LINK_REL)} was not created after Approve`);
    }

    const fileLinkLocator = page.locator('[data-testid="file-link"]');
    const fileLinkReplyDeadline = Date.now() + TIMEOUT_MS;
    let fileLinkReply = '';
    let fileLinkHtml = '';
    while (Date.now() < fileLinkReplyDeadline) {
      const count = await page.locator('[data-testid="chat-item-assistant"]').count();
      if (count > fileLinkPriorAssistants) {
        const newest = page.locator('[data-testid="chat-item-assistant"]').nth(count - 1);
        const text = (await newest.textContent())?.trim() ?? '';
        if (text.length > 0) {
          fileLinkReply = text.trim();
          fileLinkHtml = (await newest.innerHTML()) ?? '';
          if ((await fileLinkLocator.count()) > 0) break;
        }
      }
      await page.waitForTimeout(300);
    }
    const replyHasFileScheme = /file:/.test(fileLinkReply) || /file:/.test(fileLinkHtml);
    if ((await fileLinkLocator.count()) < 1) {
      throw new Error(
        `Step 17: answer had no clickable file: link after the approved write` +
          `${replyHasFileScheme ? ' (file: present in text, renderer missed it)' : ''}` +
          `. reply=${fileLinkReply} html=${fileLinkHtml.slice(0, 2000)}`,
      );
    }
    const fileLinkPath = (await fileLinkLocator.last().getAttribute('data-file-path')) ?? '';
    console.log(`Step 17 OK — answer contains a file: link (path=${fileLinkPath || '(attr unset)'}); reply: ${fileLinkReply.slice(0, 160)}`);
    await fileLinkLocator.last().click();
    await page.waitForURL(/\/files/, { timeout: 15_000 });
    const openedPath = new URL(page.url()).searchParams.get('path');
    if (openedPath !== FILE_LINK_REL) {
      throw new Error(`Step 17: expected /files?path=${FILE_LINK_REL}, got path=${openedPath} url=${page.url()}`);
    }
    const highlighted = page.locator('[data-testid="file-entry-highlighted"]');
    await highlighted.waitFor({ state: 'visible', timeout: 15_000 });
    const highlightedLabel = ((await highlighted.getAttribute('aria-label')) ?? '').trim();
    if (highlightedLabel && highlightedLabel !== 'link-test.md') {
      throw new Error(`Step 17: highlighted entry was "${highlightedLabel}", expected link-test.md`);
    }
    if (!existsSync(workspaceFilePath(FILE_LINK_REL))) {
      throw new Error(`Step 17: ${workspaceFilePath(FILE_LINK_REL)} does not exist after the approved write`);
    }
    console.log(`Step 17 OK — clicked file: link, opened /files?path=${FILE_LINK_REL}, entry highlighted`);

    // --- Step 18: voice-to-text mic (M9-06) -----------------------------
    // Dedicated https / http contexts so this does not depend on
    // CHAT_SMOKE_BASE_URL (localhost over http is a secure context).
    await assertVoiceInput(browser, contextOptions);

    const elapsedMs = Date.now() - startedAt;
    console.log(`PASS: full create -> send -> list -> reopen -> follow-up + HITL approve/reject/off + edit/regenerate + markdown + activity panel + thinking on/off + fork/switch + file-link + voice-input completed in ${elapsedMs}ms`);
  } finally {
    await browser.close();
    cleanupThreadBestEffort(threadId);
    cleanupThreadBestEffort(editThreadId);
    cleanupThreadBestEffort(forkThreadId);
    removeWorkspaceFileBestEffort(HITL_APPROVE_FILE);
    removeWorkspaceFileBestEffort(HITL_REJECT_FILE);
    removeWorkspaceFileBestEffort(HITL_OFF_FILE);
    removeWorkspaceFileBestEffort(ACTIVITY_PANEL_FILE);
    removeWorkspaceFileBestEffort(FILE_LINK_REL);
    try {
      rmdirSync(workspaceFilePath('notes'));
    } catch {
      // best-effort — leave notes/ alone if it already had other files
    }
    if (savedSettings !== null) {
      try {
        settingsRequest('PUT', {
          hitl_enabled: savedSettings.hitl_enabled,
          thinking_enabled: savedSettings.thinking_enabled,
          edit_mode_default: savedSettings.edit_mode_default,
        });
      } catch {
        // best-effort restore
      }
    }
  }
}

main().catch((error) => {
  console.error('FAIL:', error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
