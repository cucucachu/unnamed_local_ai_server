// Issue #124 full-stack image-viewer + thumbnail smoke test — invoked by
// `image_browser_smoke.sh`, not run directly.
//
// Drives the real Files UI (no mocking — real REST `/api/files*` upload,
// real `/api/media/stream` byte-range serving reused as the image source)
// through the issue's acceptance criteria:
//
//   1. Upload a tiny synthetic PNG via the existing "Upload here" button
//      (a real Chromium file chooser, intercepted by Playwright with an
//      in-memory buffer — same convention `files_browser_smoke.mjs` already
//      uses for its own text-file upload step; no ffmpeg/docker seeding
//      needed for a still image, unlike `media_browser_smoke.mjs`'s video).
//   2. The Files list shows a real thumbnail `<img>` for that row (not the
//      generic icon) once it's loaded.
//   3. Tapping the image file opens the in-app viewer DIRECTLY (bypassing
//      the action sheet, same tap-routing convention M5-02 established for
//      video/audio) -> a real `<img>` with the expected stream URL as its
//      `src`, which actually decodes (`naturalWidth > 0`) — i.e. it was
//      genuinely viewed without ever downloading the file.
//   4. Right-click (web long-press equivalent) still opens the action
//      sheet for an image file, offering "View" (not "Play") -> clicking
//      it navigates to the same viewer as the direct tap.
//
// `react-native-web`'s own `Image` implementation (confirmed by reading
// `node_modules/react-native-web/dist/exports/Image/index.js`) renders a
// `testID`-carrying OUTER `View`/`div` wrapping a CSS `background-image`
// layer PLUS a real, separate `<img src="...">` (kept for the browser's
// native "save image"/right-click context menu) — that inner `<img>` has
// no `testID` of its own, hence every locator below is
// `[data-testid="..."] img`, not the bare testID alone.

import { chromium } from 'playwright';

const BASE_URL = process.env.IMAGE_SMOKE_BASE_URL ?? 'http://localhost/';
const UI_TIMEOUT_MS = 20_000;

// 1x1 transparent PNG — the smallest real, validly-decodable PNG byte
// sequence (well-known public-domain test fixture, not hand-rolled), so
// the browser's own `<img>` decode step (`naturalWidth > 0` below) is
// checking something real rather than a fake/empty payload.
const PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';

const RUN_SUFFIX = `${process.pid}-${Date.now()}`;
const IMAGE_FILE_NAME = `image-browser-smoke-${RUN_SUFFIX}.png`;

async function waitForVisibleText(page, text, timeoutMs = UI_TIMEOUT_MS) {
  const locator = page.getByText(text, { exact: true }).first();
  await locator.waitFor({ state: 'visible', timeout: timeoutMs });
  return locator;
}

/** Polls an `<img>` locator's `src`/`naturalWidth` until `predicate`
 * passes — same manual poll-loop convention as `media_browser_smoke.mjs`'s
 * `pollVideoState` (no `@playwright/test` `expect` in this house style). */
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
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    // Right-click -> Delete's `window.confirm` (see `confirmDeleteEntry` in
    // `files.tsx`) needs auto-accepting, same as `files_browser_smoke.mjs`.
    page.on('dialog', (dialog) => dialog.accept());

    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
    await page.getByRole('tab', { name: 'Files' }).click();
    await waitForVisibleText(page, 'Home'); // confirms the screen mounted + the root dir loaded

    // --- Step 1: upload the seeded PNG via the real file chooser --------
    const [fileChooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByTestId('files-upload-button').click(),
    ]);
    await fileChooser.setFiles({
      name: IMAGE_FILE_NAME,
      mimeType: 'image/png',
      buffer: Buffer.from(PNG_BASE64, 'base64'),
    });
    await waitForVisibleText(page, IMAGE_FILE_NAME);
    console.log(`Step 1 OK — uploaded "${IMAGE_FILE_NAME}" via the real file chooser`);

    // --- Step 2: the list row shows a real, decoded thumbnail <img> -----
    const thumbnailImg = page.locator('[data-testid="file-thumbnail"] img').first();
    await thumbnailImg.waitFor({ state: 'attached', timeout: UI_TIMEOUT_MS });
    const thumbnailState = await pollImageState(
      thumbnailImg,
      (s) => s.naturalWidth > 0,
      UI_TIMEOUT_MS,
      'thumbnail <img> decoded (naturalWidth > 0)',
    );
    if (!thumbnailState.src.includes('/api/media/stream')) {
      throw new Error(`expected the thumbnail src to hit /api/media/stream, got: ${thumbnailState.src}`);
    }
    console.log('Step 2 OK — the file list shows a real, decoded thumbnail (not the generic icon)');

    // --- Step 3: tap the file -> viewer opens DIRECTLY, no action sheet -
    await page.getByText(IMAGE_FILE_NAME, { exact: true }).click();
    if ((await page.getByTestId('file-action-sheet').count()) > 0) {
      throw new Error('tapping an image file opened the action sheet instead of bypassing straight to the viewer');
    }
    const viewerImg = page.locator('[data-testid="image-viewer-image"] img').first();
    await viewerImg.waitFor({ state: 'visible', timeout: UI_TIMEOUT_MS });
    const viewerState = await pollImageState(viewerImg, (s) => s.naturalWidth > 0, UI_TIMEOUT_MS, 'viewer <img> decoded (naturalWidth > 0)');
    if (!viewerState.src.includes('/api/media/stream')) {
      throw new Error(`expected the viewer src to hit /api/media/stream, got: ${viewerState.src}`);
    }
    console.log('Step 3 OK — tapping the image opened the in-app viewer directly (no download, no action sheet) with a real decoded <img>');

    // --- Step 4: close the viewer, right-click -> action sheet's "View" -
    await page.getByTestId('media-close-button').click();
    await waitForVisibleText(page, IMAGE_FILE_NAME);
    await page.getByText(IMAGE_FILE_NAME, { exact: true }).click({ button: 'right' });
    if ((await page.getByTestId('file-action-play').count()) > 0) {
      throw new Error('the action sheet showed "Play" for an image file instead of "View"');
    }
    await page.getByTestId('file-action-view').click();
    const viewerImgAgain = page.locator('[data-testid="image-viewer-image"] img').first();
    await viewerImgAgain.waitFor({ state: 'visible', timeout: UI_TIMEOUT_MS });
    console.log('Step 4 OK — right-click action sheet offers "View" (not "Play") for an image, and it opens the same viewer');

    // --- cleanup: back to the list, right-click -> Delete ---------------
    await page.getByTestId('media-close-button').click();
    await waitForVisibleText(page, IMAGE_FILE_NAME);
    await page.getByText(IMAGE_FILE_NAME, { exact: true }).click({ button: 'right' });
    await page.getByTestId('file-action-delete').click();

    const elapsedMs = Date.now() - startedAt;
    console.log(`PASS: upload -> thumbnail -> direct-tap viewer -> right-click "View" flow completed in ${elapsedMs}ms`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error('FAIL:', error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
