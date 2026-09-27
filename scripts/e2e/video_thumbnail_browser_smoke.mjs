// Issue #125 full-stack video-thumbnail smoke test - invoked by
// video_thumbnail_browser_smoke.sh, not run directly. That wrapper
// generates video-thumbnail-browser-smoke-test-video.mp4 into a temp dir
// (via `docker run` against the homeai-exec-toolbox image, same convention
// as media_browser_smoke.sh) and passes its path in
// VIDEO_THUMBNAIL_SMOKE_FILE_PATH. This script uploads it into the e2e
// user's Personal space through the platform files API (M11-01).
//
// Drives the real Files UI (no mocking - real REST /api/platform/files list,
// real server-side ffmpeg-generated GET /api/platform/files/thumbnail poster
// frame) through this issue's acceptance criteria:
//
//   1. The Files list shows a real, decoded thumbnail <img> for the
//      seeded video row (not the generic videocam-outline icon) once it
//      loads - proving a REAL frame was extracted server-side via
//      ffmpeg, not a placeholder.
//   2. That thumbnail is actually cached: a second load of the same list
//      (re-navigating away and back to the Files tab) reuses the exact
//      same thumbnail URL and still decodes - this script can't directly
//      observe "no ffmpeg re-invocation happened" from the browser side
//      (that's test_media_thumbnail.py's own
//      test_thumbnail_second_request_is_cache_hit_not_regenerated job, a
//      real unit-level assertion with a call-counting spy), but it DOES
//      prove the end-to-end contract a user actually experiences: the
//      thumbnail keeps working and keeps loading fast on repeat visits.
//   3. Tapping the video file still opens the real <video> media player
//      directly (M5-02's existing tap-routing, already covered end-to-end
//      by media_browser_smoke.mjs - re-asserted here only as a one-line
//      smoke check that adding the thumbnail didn't regress the existing
//      play flow, not a full re-test of play/seek).
//
// Same react-native-web Image DOM shape as image_browser_smoke.mjs relies
// on (see that file's own header comment for the
// node_modules/react-native-web/dist/exports/Image/index.js citation): an
// outer testID-carrying View/div wrapping a separate, untagged
// <img src="..."> - hence every locator below is
// [data-testid="..."] img, not the bare testID alone.

import { chromium } from 'playwright';

import { readFileSync } from 'node:fs';
import { basename } from 'node:path';

import { createE2eUser, deleteE2eUsers, loginThroughUi, sessionCookie } from './auth_helpers.mjs';
import { openSpace, uploadFile } from './files_helpers.mjs';

const BASE_URL = process.env.VIDEO_THUMBNAIL_SMOKE_BASE_URL ?? 'http://localhost/';
const VIDEO_FILE_PATH = process.env.VIDEO_THUMBNAIL_SMOKE_FILE_PATH;
if (!VIDEO_FILE_PATH) {
  throw new Error('VIDEO_THUMBNAIL_SMOKE_FILE_PATH is not set (run video_thumbnail_browser_smoke.sh)');
}
const VIDEO_FILE_NAME = basename(VIDEO_FILE_PATH);
const UI_TIMEOUT_MS = 20_000;

async function waitForVisibleText(page, text, timeoutMs = UI_TIMEOUT_MS) {
  const locator = page.getByText(text, { exact: true }).first();
  await locator.waitFor({ state: 'visible', timeout: timeoutMs });
  return locator;
}

// Polls an <img> locator's src/naturalWidth until predicate passes - same
// manual poll-loop convention as image_browser_smoke.mjs's
// pollImageState (no @playwright/test expect in this house style).
async function pollImageState(imgLocator, predicate, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    last = await imgLocator.evaluate((el) => ({ src: el.src, naturalWidth: el.naturalWidth }));
    if (predicate(last)) return last;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`${label} not satisfied within ${timeoutMs}ms (last state: ${JSON.stringify(last)})`);
}

async function main() {
  const startedAt = Date.now();
  const e2eUser = createE2eUser({ prefix: 'e2e-videothumb' });
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
    await loginThroughUi(page, e2eUser);
    const cookie = await sessionCookie(page.context());
    await uploadFile(cookie, '/personal', VIDEO_FILE_NAME, readFileSync(VIDEO_FILE_PATH), 'video/mp4');

    await page.getByRole('tab', { name: 'Files' }).click();
    await waitForVisibleText(page, 'Personal'); // confirms the screen mounted + the root loaded
    await openSpace(page, 'Personal');
    await waitForVisibleText(page, VIDEO_FILE_NAME); // confirms the seeded file is listed

    // --- Step 1: the list row shows a real, decoded thumbnail <img> -----
    const thumbnailImg = page.locator('[data-testid="video-thumbnail"] img').first();
    await thumbnailImg.waitFor({ state: 'attached', timeout: UI_TIMEOUT_MS });
    const thumbnailState = await pollImageState(
      thumbnailImg,
      (s) => s.naturalWidth > 0,
      UI_TIMEOUT_MS,
      'thumbnail <img> decoded (naturalWidth > 0)',
    );
    if (!thumbnailState.src.includes('/api/platform/files/thumbnail')) {
      throw new Error(`expected the thumbnail src to hit /api/platform/files/thumbnail, got: ${thumbnailState.src}`);
    }
    console.log('Step 1 OK - the file list shows a real, ffmpeg-generated, decoded thumbnail (not the generic icon)');

    // --- Step 2: re-navigate away and back -> thumbnail still loads -----
    // (same URL, server-side cache hit per app/core/thumbnails.py's
    // get_cached_thumbnail - this just proves the end-to-end contract
    // still works on a second real page load, not the cache internals.)
    await page.getByRole('tab', { name: 'Chat' }).click();
    await page.getByRole('tab', { name: 'Files' }).click();
    await waitForVisibleText(page, VIDEO_FILE_NAME);

    const thumbnailImgAgain = page.locator('[data-testid="video-thumbnail"] img').first();
    await thumbnailImgAgain.waitFor({ state: 'attached', timeout: UI_TIMEOUT_MS });
    const secondState = await pollImageState(
      thumbnailImgAgain,
      (s) => s.naturalWidth > 0,
      UI_TIMEOUT_MS,
      'thumbnail <img> decoded again after re-navigating (naturalWidth > 0)',
    );
    if (secondState.src !== thumbnailState.src) {
      throw new Error(
        `expected the same cached thumbnail URL on revisit, got "${secondState.src}" vs original "${thumbnailState.src}"`,
      );
    }
    console.log('Step 2 OK - thumbnail still loads (same URL, server-side cache) after re-navigating away and back');

    // --- Step 3: tapping the video still opens the real player directly -
    await page.getByText(VIDEO_FILE_NAME, { exact: true }).click();
    const videoLocator = page.locator('[data-testid="media-player-video"]');
    await videoLocator.waitFor({ state: 'visible', timeout: UI_TIMEOUT_MS });
    const tagName = await videoLocator.evaluate((el) => el.tagName);
    if (tagName !== 'VIDEO') {
      throw new Error(`expected a real <video> element, got <${tagName}>`);
    }
    console.log('Step 3 OK - tapping the video still opens the real media player directly (thumbnail did not regress playback)');

    const elapsedMs = Date.now() - startedAt;
    console.log(`PASS: list thumbnail -> cached-on-revisit -> direct-tap playback flow completed in ${elapsedMs}ms`);
  } finally {
    await browser.close();
    deleteE2eUsers(e2eUser.username);
  }
}

main().catch((error) => {
  console.error('FAIL:', error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
