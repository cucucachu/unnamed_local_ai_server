// M5-02 full-stack media-player smoke test — invoked by
// `media_browser_smoke.sh`, not run directly. That wrapper seeds
// `test-video.mp4` into the workspace (via `docker run` against the
// `homeai-exec-toolbox` image, since ffmpeg lives there, not necessarily on
// this host — same tool M5-01 used for its own live verification) BEFORE
// calling this script, and cleans it up after — those steps live in the
// `.sh` wrapper (not here) because they need `docker`/the workspace's real
// host path, neither of which this script has any other reason to reach
// for itself. See that file for the exact `ffmpeg`/cleanup commands.
//
// Drives the real Files UI (no mocking — real REST `/api/files` list, real
// `/api/media/stream` Range-streamed playback) through M5-02's acceptance
// criteria:
//
//   1. Tap the seeded video file in the Files tab -> per M5-02's tap-
//      routing, a recognized media extension opens the player modal
//      DIRECTLY (no action sheet) -> assert a real `<video>` DOM element
//      appears (the web export's `MediaPlayer.web.tsx` renders a plain
//      `<video controls>` — no mocking of the player itself).
//   2. `currentTime` advances after invoking `.play()` (script-driven, via
//      `page.evaluate` — explicitly sanctioned by the ticket's own wording:
//      "Playwright can call `page.evaluate` to invoke `.play()` and read
//      `.currentTime`").
//   3. Setting `currentTime = 5` (script-driven seek) results in playback
//      resuming from ~5s: `readyState` recovers (>= `HAVE_CURRENT_DATA`)
//      and no `error` event fires.

import { chromium } from 'playwright';

const BASE_URL = process.env.MEDIA_SMOKE_BASE_URL ?? 'http://localhost/';
const VIDEO_FILE_NAME = process.env.MEDIA_SMOKE_FILE_NAME ?? 'test-video.mp4';
const UI_TIMEOUT_MS = 20_000;

async function waitForVisibleText(page, text, timeoutMs = UI_TIMEOUT_MS) {
  const locator = page.getByText(text, { exact: true }).first();
  await locator.waitFor({ state: 'visible', timeout: timeoutMs });
  return locator;
}

/** One `page.evaluate` read of the located `<video>` element's playback
 * state — kept as a single round trip (rather than several) so each poll
 * iteration below sees an internally-consistent snapshot. */
async function readVideoState(videoLocator) {
  return videoLocator.evaluate((el) => ({
    tagName: el.tagName,
    currentTime: el.currentTime,
    readyState: el.readyState,
    paused: el.paused,
    error: el.error ? `${el.error.code}: ${el.error.message}` : null,
  }));
}

/** Polls `readVideoState` until `predicate` passes — same manual poll-loop
 * convention as `chat_browser_smoke.mjs`'s `expectCountAbove`/
 * `waitForAnyText` (no `@playwright/test` `expect` in this house style).
 * Returns the last-read state on success, so callers can log/compare it. */
async function pollVideoState(page, videoLocator, predicate, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    last = await readVideoState(videoLocator);
    if (predicate(last)) return last;
    await page.waitForTimeout(300);
  }
  throw new Error(`${label} not satisfied within ${timeoutMs}ms (last state: ${JSON.stringify(last)})`);
}

async function main() {
  const startedAt = Date.now();
  // `--autoplay-policy=no-user-gesture-required`: without it, Chromium may
  // block a script-invoked `.play()` on an unmuted `<video>` depending on
  // how much "user activation" is left over from the file-row click that
  // opened the modal — this test's own script-driven `.play()`/seek calls
  // (explicitly sanctioned by the ticket) shouldn't be flaky on that browser
  // policy, which has nothing to do with what's actually being tested here.
  const browser = await chromium.launch({ headless: true, args: ['--autoplay-policy=no-user-gesture-required'] });
  try {
    const page = await browser.newPage();
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });

    await page.getByRole('tab', { name: 'Files' }).click();
    await waitForVisibleText(page, 'Home'); // confirms the screen mounted + the root dir loaded

    // --- Step 1: tap the seeded video file -> media modal opens directly ---
    await page.getByText(VIDEO_FILE_NAME, { exact: true }).click();

    const videoLocator = page.locator('[data-testid="media-player-video"]');
    await videoLocator.waitFor({ state: 'visible', timeout: UI_TIMEOUT_MS });
    const mountedState = await readVideoState(videoLocator);
    if (mountedState.tagName !== 'VIDEO') {
      throw new Error(`expected a real <video> element, got <${mountedState.tagName}>`);
    }
    console.log('Step 1 OK — tapping the video file opened the media modal directly with a real <video> element');

    // --- Step 2: currentTime advances after play() ----------------------
    await videoLocator.evaluate((el) => el.play());
    const ready = await pollVideoState(page, videoLocator, (s) => s.readyState >= 2, UI_TIMEOUT_MS, 'video became ready to play (readyState >= 2)');
    await pollVideoState(
      page,
      videoLocator,
      (s) => s.currentTime > ready.currentTime,
      UI_TIMEOUT_MS,
      'currentTime advanced after play()',
    );
    console.log('Step 2 OK — currentTime advances after play()');

    // --- Step 3: script-driven seek to ~5s, playback resumes cleanly ----
    await videoLocator.evaluate((el) => {
      el.currentTime = 5;
    });
    const afterSeek = await pollVideoState(
      page,
      videoLocator,
      (s) => s.currentTime >= 4.5 && s.readyState >= 2 && s.error === null,
      UI_TIMEOUT_MS,
      'playback resumed from ~5s (readyState recovered, no error)',
    );
    console.log(
      `Step 3 OK — seeked to currentTime=${afterSeek.currentTime.toFixed(2)}s, readyState=${afterSeek.readyState}, error=${afterSeek.error}`,
    );

    const elapsedMs = Date.now() - startedAt;
    console.log(`PASS: tap-to-play -> play -> script-driven seek flow completed in ${elapsedMs}ms`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error('FAIL:', error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
