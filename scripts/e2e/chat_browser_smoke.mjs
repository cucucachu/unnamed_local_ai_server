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

import { chromium } from 'playwright';

const BASE_URL = process.env.CHAT_SMOKE_BASE_URL ?? 'http://localhost/';
const TIMEOUT_MS = 120_000;

// Deliberately > 60 chars (and with a run of internal whitespace) so the
// list-title assertion below actually exercises `_derive_title`'s
// whitespace-collapse + 60-char-ellipsis truncation, not just the
// "message became the title" happy path.
const FIRST_MESSAGE =
  'Say exactly: PONG. This message   is intentionally padded well past sixty characters so the thread list title gets truncated with an ellipsis.';
const FOLLOW_UP_MESSAGE = 'Say exactly: PONG again please.';
const STREAMING_CURSOR = '▍'; // see `STREAMING_CURSOR` in chat/[threadId].tsx

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
        if (!text.includes(STREAMING_CURSOR)) break; // streaming finished
      }
    }
    await page.waitForTimeout(300);
  }

  if (!sawNonEmptyText) {
    throw new Error(`no assistant bubble with text appeared within ${TIMEOUT_MS}ms of sending "${message}"`);
  }
  return replyText.replace(STREAMING_CURSOR, '').trim();
}

/** Polls until an element with exact text `text` is visible (used for the
 * thread-list title assertion, and for the "back on the list screen"/"back
 * on the thread screen" navigation waits below). */
async function waitForText(page, text, timeoutMs = 15_000) {
  const locator = page.getByText(text, { exact: true }).first();
  await locator.waitFor({ state: 'visible', timeout: timeoutMs });
  return locator;
}

async function main() {
  const startedAt = Date.now();
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });

    // Explicit navigation to the Chat tab (even though it's also the `/`
    // redirect target today — see `src/app/(tabs)/index.tsx` — so this
    // script keeps working if that default ever changes).
    await page.getByRole('tab', { name: 'Chat' }).click();

    // --- Step 1: create a new chat from the UI -------------------------
    const newChatButton = page.locator('[data-testid="new-chat-header-button"]');
    await newChatButton.waitFor({ state: 'visible', timeout: 15_000 });
    await newChatButton.click();

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

    const elapsedMs = Date.now() - startedAt;
    console.log(`PASS: full create -> send -> list -> reopen -> follow-up flow completed in ${elapsedMs}ms`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error('FAIL:', error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
