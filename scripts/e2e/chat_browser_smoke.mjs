// M2-06 full-stack chat smoke test — invoked by `chat_browser_smoke.sh`, not
// run directly (that script sets up `node_modules`/browsers first). Opens a
// real headless Chromium against the live stack, drives the actual Chat UI
// (no mocking — real WebSocket, real agent-server, real model), and asserts
// a real assistant bubble with text appears.
//
// Mirrors the spirit of `scripts/ws_smoke.py` (same "Say exactly: PONG"
// prompt, same tolerance for the real model not saying exactly that) but at
// the browser/UI layer instead of raw WebSocket frames.

import { chromium } from 'playwright';

const BASE_URL = process.env.CHAT_SMOKE_BASE_URL ?? 'http://localhost/';
const PROMPT = 'Say exactly: PONG';
const TIMEOUT_MS = 120_000;

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

    const input = page.getByPlaceholder('Message…');
    await input.waitFor({ state: 'visible', timeout: 15_000 });

    // Count existing assistant bubbles first so we can unambiguously wait
    // for a *new* one below rather than matching a bubble left over from a
    // previous run against the same (default) thread. `testID="chat-item-*"`
    // (set in `src/app/(tabs)/chat.tsx`) maps to `data-testid` via
    // react-native-web.
    const assistantBubbleLocator = page.locator('[data-testid="chat-item-assistant"]');
    const priorAssistantCount = await assistantBubbleLocator.count();

    await input.fill(PROMPT);
    const sendButton = page.getByRole('button', { name: 'Send message' });
    await sendButton.click();

    // `▍` (see `STREAMING_CURSOR` in chat.tsx) is rendered only while the
    // item's `streaming` flag is true — its absence is how this script
    // knows the reply is done, without needing to poke at React state.
    const STREAMING_CURSOR = '▍';

    const deadline = Date.now() + TIMEOUT_MS;
    let assistantText = '';
    let sawNonEmptyText = false;
    while (Date.now() < deadline) {
      const count = await assistantBubbleLocator.count();
      if (count > priorAssistantCount) {
        const newest = assistantBubbleLocator.nth(count - 1);
        const text = (await newest.textContent())?.trim() ?? '';
        if (text.length > 0) {
          sawNonEmptyText = true;
          assistantText = text;
          if (!text.includes(STREAMING_CURSOR)) break; // streaming finished
        }
      }
      await page.waitForTimeout(300);
    }

    if (!sawNonEmptyText) {
      throw new Error(
        `no assistant bubble with text appeared within ${TIMEOUT_MS}ms of sending "${PROMPT}"`,
      );
    }
    // Streaming may not have finished by the deadline — that's fine, the
    // acceptance criterion is just "an assistant bubble containing text
    // appears"; strip a lingering cursor glyph so the reported text is clean.
    assistantText = assistantText.replace(STREAMING_CURSOR, '').trim();

    const elapsedMs = Date.now() - startedAt;
    console.log(`PASS: assistant replied within ${elapsedMs}ms`);
    console.log(`Assistant response: ${assistantText}`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error('FAIL:', error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
