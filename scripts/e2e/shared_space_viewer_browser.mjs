// M11-05 (GATE G11): the viewer's side of the shared-space scenario in a
// real headless browser — invoked by `shared_space_viewer_smoke.sh`, which
// creates the users and the space and has A's agent write the note first.
//
// As viewer B: the Files root lists Personal and the shared space; B's
// Personal doesn't show A's private file; the space lists A's note with the
// read-only UI (no upload/new folder; the action sheet offers only
// Download), and Download from the UI yields A's exact marker text.

import { readFile } from 'node:fs/promises';

import { chromium } from 'playwright';

import { loginThroughUi } from './auth_helpers.mjs';
import { goToFilesRoot, openSpace } from './files_helpers.mjs';
import { openSystemApp } from './nav_helpers.mjs';

const BASE_URL = process.env.FILES_SMOKE_BASE_URL ?? 'http://localhost/';
const UI_TIMEOUT_MS = 20_000;
const { G11_VIEWER, G11_VIEWER_PASSWORD, G11_SPACE_NAME, NOTE_NAME, MARKER, PRIVATE_NAME } = process.env;

async function visible(page, text) {
  await page.getByText(text, { exact: true }).first().waitFor({ state: 'visible', timeout: UI_TIMEOUT_MS });
}

async function absent(page, testIds, what) {
  for (const testId of testIds) {
    if ((await page.getByTestId(testId).count()) > 0) throw new Error(`${what} offers ${testId}`);
  }
}

/** Download goes through `window.open` on web, so the download event fires
 * on a popup page rather than on `page` itself. */
function nextDownload(context, page) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('no download started')), UI_TIMEOUT_MS);
    const done = (download) => {
      clearTimeout(timer);
      resolve(download);
    };
    page.on('download', done);
    context.on('page', (popup) => popup.on('download', done));
  });
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ acceptDownloads: true });
    const page = await context.newPage();
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
    await loginThroughUi(page, { username: G11_VIEWER, password: G11_VIEWER_PASSWORD });
    await openSystemApp(page, 'files');
    await goToFilesRoot(page);
    await visible(page, 'Personal');
    await visible(page, G11_SPACE_NAME);
    console.log(`OK B's Files root lists Personal and "${G11_SPACE_NAME}"`);

    await openSpace(page, 'Personal');
    await visible(page, 'This folder is empty');
    if ((await page.getByText(PRIVATE_NAME, { exact: true }).count()) > 0) {
      throw new Error(`B's Personal shows A's private file ${PRIVATE_NAME}`);
    }
    console.log(`OK B's Personal is empty (A's ${PRIVATE_NAME} not visible)`);

    await openSpace(page, G11_SPACE_NAME);
    await visible(page, NOTE_NAME);
    await page.getByTestId('files-read-only').first().waitFor({ state: 'visible', timeout: UI_TIMEOUT_MS });
    await absent(page, ['files-upload-button', 'files-new-folder-button'], 'the shared space');
    await page.getByText(NOTE_NAME, { exact: true }).click({ button: 'right' });
    await page.getByTestId('file-action-download').waitFor({ state: 'visible', timeout: UI_TIMEOUT_MS });
    await absent(page, ['rename', 'move', 'copy', 'delete'].map((a) => `file-action-${a}`), "B's action sheet");
    console.log(`OK "${G11_SPACE_NAME}" is read-only for B: no upload/new folder, only Download`);

    const download = nextDownload(context, page);
    await page.getByTestId('file-action-download').click();
    const text = (await readFile(await (await download).path(), 'utf8')).trim();
    if (text !== MARKER) throw new Error(`downloaded ${NOTE_NAME} reads ${JSON.stringify(text)}, expected ${MARKER}`);
    console.log(`OK B downloaded ${NOTE_NAME} from the Files UI: ${JSON.stringify(text)}`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error('FAIL:', error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
