// M3-05 full-stack files-browser smoke test — invoked by
// `files_browser_smoke.sh`, not run directly (that script sets up
// `node_modules`/browsers first).
//
// Opens a real headless Chromium against the live stack (no mocking — real
// `/api/platform/files*` calls against the platform's space directories),
// drives the actual Files UI, and runs the FULL flow from the ticket's
// acceptance criteria TWICE inside the user's Personal space:
//
//   1. Plain ASCII names (`e2e-dir`, matching the ticket's own literal
//      example).
//   2. A folder AND a file name that both contain a space and non-ASCII
//      characters (`тест файл.txt`, per the ticket) — so every UI action
//      below (mkdir, breadcrumb navigate, upload, rename [= move], the
//      REST verification GET, right-click delete, and the "gone" REST GET)
//      is exercised against a properly space-and-unicode-containing path,
//      not just the folder OR the file individually.
//
// M11-01 (spaces): the root lists Personal plus each shared space. The user
// owns a throwaway shared space, so the root check sees both. The ASCII pass
// also moves its renamed file into that space through the destination
// picker (a cross-space move) before deleting the folder. Last, a second
// user who is only a viewer of the space opens it and must get the
// read-only UI.
//
// Each pass:
//   a. Create a folder from the UI ("New folder" -> prompt dialog).
//   b. Descend into it (tap the row).
//   c. Upload a small file into it ("Upload here" -> real Chromium file
//      chooser, intercepted by Playwright — no OS-level dialog).
//   d. Tap the file (opens the action sheet) -> Rename -> prompt dialog.
//   e. Verify the rename via a RAW REST GET, decoupled from the UI's own
//      (re-fetched, but still client-rendered) state.
//   f. Back to Personal, right-click the folder (directories only open the
//      action sheet via long-press/right-click, since tapping one
//      descends) -> Delete -> confirm (`window.confirm`, auto-accepted via
//      a `page.on('dialog', ...)` handler registered up front).
//   g. Verify it's gone via another raw REST GET.

import { chromium } from 'playwright';

import { createE2eUser, deleteE2eSpaces, deleteE2eUsers, loginThroughUi, sessionCookie } from './auth_helpers.mjs';
import { addMember, createSpace, deleteBestEffort, entryNames, listDir, openSpace } from './files_helpers.mjs';

const BASE_URL = process.env.FILES_SMOKE_BASE_URL ?? 'http://localhost/';
const UI_TIMEOUT_MS = 20_000;

const RUN_SUFFIX = `${process.pid}-${Date.now()}`;
const SPACE_SLUG = process.env.FILES_SMOKE_SPACE_SLUG ?? `e2e-files-${Math.random().toString(16).slice(2, 8)}`;
const SPACE_NAME = `E2E Files ${RUN_SUFFIX}`;

const ASCII_FLOW = {
  folderName: `e2e-dir-${RUN_SUFFIX}`,
  fileName: 'e2e-file.txt',
  renamedFileName: 'e2e-file-renamed.txt',
  fileContent: 'files-browser-smoke ascii pass\n',
  moveToSpace: true,
};

// Deliberately both space- AND non-ASCII-containing at every level (folder
// name, original file name, renamed file name) — see the file header.
const UNICODE_FLOW = {
  folderName: `e2e папка тест ${RUN_SUFFIX}`,
  fileName: 'тест файл.txt',
  renamedFileName: 'тест файл переименован.txt',
  fileContent: 'files-browser-smoke unicode+space pass\n',
  moveToSpace: false,
};

/** Polls `locator.count()` until it's zero — used instead of Playwright's
 * built-in `waitFor({state: 'hidden'})`, which requires the element to
 * still exist in the DOM (just display:none/etc); a deleted row is
 * removed from the DOM entirely once the list re-renders. */
async function waitForGone(page, text, timeoutMs = UI_TIMEOUT_MS) {
  const locator = page.getByText(text, { exact: true });
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if ((await locator.count()) === 0) return;
    await page.waitForTimeout(200);
  }
  throw new Error(`"${text}" was still present after ${timeoutMs}ms`);
}

async function waitForVisibleText(page, text, timeoutMs = UI_TIMEOUT_MS) {
  const locator = page.getByText(text, { exact: true }).first();
  await locator.waitFor({ state: 'visible', timeout: timeoutMs });
  return locator;
}

async function runFullFlow(page, cookie, { folderName, fileName, renamedFileName, fileContent, moveToSpace }) {
  console.log(`--- flow: folder="${folderName}" file="${fileName}" -> "${renamedFileName}" ---`);
  const folderPath = `/personal/${folderName}`;

  // --- create the folder --------------------------------------------------
  await page.getByTestId('files-new-folder-button').click();
  await page.getByTestId('prompt-modal-input').fill(folderName);
  await page.getByTestId('prompt-modal-submit').click();
  await waitForVisibleText(page, folderName);
  console.log(`  OK created folder "${folderName}"`);

  // --- descend into it -----------------------------------------------------
  await page.getByText(folderName, { exact: true }).click();
  await waitForVisibleText(page, 'This folder is empty');
  console.log('  OK descended into it (confirmed empty)');

  // --- upload a small file into it -----------------------------------------
  const [fileChooser] = await Promise.all([
    page.waitForEvent('filechooser'),
    page.getByTestId('files-upload-button').click(),
  ]);
  // A custom {name, mimeType, buffer} payload (rather than a real path on
  // disk) lets the uploaded file's displayed name be exactly `fileName`
  // (including spaces/non-ASCII) with no host-filesystem-encoding concerns.
  await fileChooser.setFiles({ name: fileName, mimeType: 'text/plain', buffer: Buffer.from(fileContent, 'utf-8') });
  await waitForVisibleText(page, fileName);
  console.log(`  OK uploaded "${fileName}"`);

  // --- tap the file -> action sheet -> Rename ------------------------------
  await page.getByText(fileName, { exact: true }).click();
  await page.getByTestId('file-action-rename').click();
  await page.getByTestId('prompt-modal-input').fill(renamedFileName);
  await page.getByTestId('prompt-modal-submit').click();
  await waitForVisibleText(page, renamedFileName);
  await waitForGone(page, fileName);
  console.log(`  OK renamed "${fileName}" -> "${renamedFileName}"`);

  // --- verify the rename via a raw REST GET ---------------------------------
  const namesAfterRename = entryNames(await listDir(cookie, folderPath));
  if (!namesAfterRename.includes(renamedFileName)) {
    throw new Error(`REST GET ${folderPath} did not include "${renamedFileName}": ${JSON.stringify(namesAfterRename)}`);
  }
  if (namesAfterRename.includes(fileName)) {
    throw new Error(`REST GET ${folderPath} still included the old name "${fileName}": ${JSON.stringify(namesAfterRename)}`);
  }
  console.log(`  OK verified rename via REST GET ${folderPath}`);

  // --- cross-space move through the destination picker ---------------------
  if (moveToSpace) {
    await page.getByText(renamedFileName, { exact: true }).click();
    await page.getByTestId('file-action-move').click();
    // The picker opens in the current folder; up twice reaches the root.
    await page.getByTestId('destination-picker-up').click();
    await page.getByTestId('destination-picker-up').click();
    await page.getByText(SPACE_NAME, { exact: true }).last().click();
    await page.getByTestId('destination-picker-select').click();
    await waitForGone(page, renamedFileName);
    const inSpace = entryNames(await listDir(cookie, `/spaces/${SPACE_SLUG}`));
    if (!inSpace.includes(renamedFileName)) {
      throw new Error(`"${renamedFileName}" is not in /spaces/${SPACE_SLUG} after the move: ${JSON.stringify(inSpace)}`);
    }
    console.log(`  OK moved "${renamedFileName}" into the shared space "${SPACE_NAME}" (destination picker)`);
  }

  // --- back to Personal, right-click the folder -> Delete -> confirm -------
  await openSpace(page, 'Personal');
  await waitForVisibleText(page, folderName);
  await page.getByText(folderName, { exact: true }).click({ button: 'right' });
  await page.getByTestId('file-action-delete').click();
  await waitForGone(page, folderName);
  console.log(`  OK deleted folder "${folderName}" (right-click -> action sheet -> Delete -> confirm)`);

  // --- verify gone via a raw REST GET on the space root --------------------
  if (entryNames(await listDir(cookie, '/personal')).includes(folderName)) {
    throw new Error(`REST GET /personal still included "${folderName}" after delete`);
  }
  console.log('  OK verified gone via REST GET /personal');
}

async function checkViewerIsReadOnly(browser, viewer) {
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
    await loginThroughUi(page, viewer);
    await page.getByRole('tab', { name: 'Files' }).click();
    await waitForVisibleText(page, SPACE_NAME);
    await openSpace(page, SPACE_NAME);
    await waitForVisibleText(page, ASCII_FLOW.renamedFileName);
    await page.getByTestId('files-read-only').first().waitFor({ state: 'visible', timeout: UI_TIMEOUT_MS });
    for (const testId of ['files-upload-button', 'files-new-folder-button']) {
      if ((await page.getByTestId(testId).count()) > 0) throw new Error(`viewer sees ${testId}`);
    }
    await page.getByText(ASCII_FLOW.renamedFileName, { exact: true }).click({ button: 'right' });
    await page.getByTestId('file-action-download').waitFor({ state: 'visible', timeout: UI_TIMEOUT_MS });
    for (const action of ['rename', 'move', 'copy', 'delete']) {
      if ((await page.getByTestId(`file-action-${action}`).count()) > 0) {
        throw new Error(`viewer's action sheet offers "${action}"`);
      }
    }
    console.log(`OK viewer sees "${SPACE_NAME}" read-only: no upload/new folder, only Download in the action sheet`);
  } finally {
    await context.close();
  }
}

async function main() {
  const startedAt = Date.now();

  const { FILES_SMOKE_USER, FILES_SMOKE_PASSWORD, FILES_SMOKE_VIEWER, FILES_SMOKE_VIEWER_PASSWORD } = process.env;
  const e2eUser = FILES_SMOKE_USER
    ? { username: FILES_SMOKE_USER, password: FILES_SMOKE_PASSWORD }
    : createE2eUser({ prefix: 'e2e-files' });
  const viewer = FILES_SMOKE_VIEWER
    ? { username: FILES_SMOKE_VIEWER, password: FILES_SMOKE_VIEWER_PASSWORD }
    : createE2eUser({ prefix: 'e2e-files-viewer' });
  let cookie = null;

  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    // `Delete` uses `window.confirm` on web (see `confirmDeleteEntry` in
    // `files.tsx`) — Playwright auto-DISMISSES native dialogs unless a
    // handler is registered, so this must be set up before any Delete click.
    page.on('dialog', (dialog) => dialog.accept());

    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });

    await loginThroughUi(page, e2eUser);
    cookie = await sessionCookie(page.context());
    const space = await createSpace(cookie, SPACE_SLUG, SPACE_NAME);
    await addMember(cookie, space.id, viewer.username, 'viewer');

    await page.getByRole('tab', { name: 'Files' }).click();
    await waitForVisibleText(page, 'Personal');
    await waitForVisibleText(page, SPACE_NAME);
    for (const testId of ['files-upload-button', 'files-new-folder-button']) {
      if ((await page.getByTestId(testId).count()) > 0) throw new Error(`the root offers ${testId}`);
    }
    console.log(`OK the root lists Personal and "${SPACE_NAME}", with no write actions`);

    await openSpace(page, 'Personal');
    await runFullFlow(page, cookie, ASCII_FLOW);
    await runFullFlow(page, cookie, UNICODE_FLOW);
    await checkViewerIsReadOnly(browser, viewer);

    const elapsedMs = Date.now() - startedAt;
    console.log(`PASS: both flows (ASCII + space/non-ASCII), cross-space move, viewer read-only in ${elapsedMs}ms`);
  } finally {
    await browser.close();
    if (cookie) {
      await deleteBestEffort(cookie, `/personal/${ASCII_FLOW.folderName}`);
      await deleteBestEffort(cookie, `/personal/${UNICODE_FLOW.folderName}`);
    }
    // Shared spaces have no `owner_user_id`, so deleting the users would not
    // take the space with them.
    deleteE2eSpaces(SPACE_SLUG);
    deleteE2eUsers(e2eUser.username, viewer.username);
  }
}

main().catch((error) => {
  console.error('FAIL:', error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
