// M3-05 full-stack files-browser smoke test — invoked by
// `files_browser_smoke.sh`, not run directly (that script sets up
// `node_modules`/browsers first).
//
// Opens a real headless Chromium against the live stack (no mocking — real
// REST `/api/files*` calls against `agent-server`'s workspace directory),
// drives the actual Files UI, and runs the FULL flow from the ticket's
// acceptance criteria TWICE:
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
// Each pass:
//   a. Create a folder from the UI ("New folder" -> prompt dialog).
//   b. Descend into it (tap the row).
//   c. Upload a small file into it ("Upload here" -> real Chromium file
//      chooser, intercepted by Playwright — no OS-level dialog).
//   d. Tap the file (opens the action sheet) -> Rename -> prompt dialog.
//   e. Verify the rename via a RAW `GET /api/files` — decoupled from the
//      UI's own (re-fetched, but still client-rendered) state — using
//      Python's `urllib.request`, same curl-equivalent house pattern as
//      `files_rest_smoke.sh` (`curl` is not installed on this host).
//   f. Back to Home, right-click the folder (directories only open the
//      action sheet via long-press/right-click, since tapping one
//      descends) -> Delete -> confirm (`window.confirm`, auto-accepted via
//      a `page.on('dialog', ...)` handler registered up front).
//   g. Verify it's gone via another raw REST GET on the root.
//
// Best-effort pre/post cleanup (via the same REST DELETE helper) makes this
// script safely re-runnable even after a prior failed run left state behind
// — same "idempotent, re-runnable" convention as `files_rest_smoke.sh`.

import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';

const BASE_URL = process.env.FILES_SMOKE_BASE_URL ?? 'http://localhost/';
const API_BASE = process.env.FILES_SMOKE_API_BASE ?? 'http://localhost/api';
const UI_TIMEOUT_MS = 20_000;

const RUN_SUFFIX = `${process.pid}-${Date.now()}`;

const ASCII_FLOW = {
  folderName: `e2e-dir-${RUN_SUFFIX}`,
  fileName: 'e2e-file.txt',
  renamedFileName: 'e2e-file-renamed.txt',
  fileContent: 'files-browser-smoke ascii pass\n',
};

// Deliberately both space- AND non-ASCII-containing at every level (folder
// name, original file name, renamed file name) — see the file header.
const UNICODE_FLOW = {
  folderName: `e2e папка тест ${RUN_SUFFIX}`,
  fileName: 'тест файл.txt',
  renamedFileName: 'тест файл переименован.txt',
  fileContent: 'files-browser-smoke unicode+space pass\n',
};

/** Runs a small inline Python script (`urllib.request`) and returns stdout.
 * Same "curl is not installed on this host, urllib is the house
 * curl-equivalent" convention as `files_rest_smoke.sh`. */
function runPython(script, args) {
  return execFileSync('python3', ['-c', script, ...args], { encoding: 'utf-8' });
}

const LIST_SCRIPT = `
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

url = sys.argv[1] + '?' + urllib.parse.urlencode({'path': sys.argv[2]})
try:
    with urllib.request.urlopen(url, timeout=15) as resp:
        print(resp.read().decode())
except urllib.error.HTTPError as e:
    print(json.dumps({"entries": [], "_status": e.code}))
`;

const DELETE_SCRIPT = `
import sys
import urllib.error
import urllib.parse
import urllib.request

url = sys.argv[1] + '?' + urllib.parse.urlencode({'path': sys.argv[2]})
req = urllib.request.Request(url, method='DELETE')
try:
    urllib.request.urlopen(req, timeout=15)
except urllib.error.HTTPError:
    pass  # best-effort — fine if it's already gone (404) or was never created
`;

function restListFiles(relPath) {
  const output = runPython(LIST_SCRIPT, [`${API_BASE}/files`, relPath]);
  return JSON.parse(output);
}

function restDeleteBestEffort(relPath) {
  runPython(DELETE_SCRIPT, [`${API_BASE}/files`, relPath]);
}

function entryNames(listing) {
  return listing.entries.map((entry) => entry.name);
}

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

async function goHome(page) {
  await page.getByText('Home', { exact: true }).click();
}

async function runFullFlow(page, { folderName, fileName, renamedFileName, fileContent }) {
  console.log(`--- flow: folder="${folderName}" file="${fileName}" -> "${renamedFileName}" ---`);

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

  // --- verify the rename via a raw REST GET (Python urllib) ---------------
  const afterRename = restListFiles(folderName);
  const namesAfterRename = entryNames(afterRename);
  if (!namesAfterRename.includes(renamedFileName)) {
    throw new Error(
      `REST GET /api/files?path=${folderName} did not include "${renamedFileName}": ${JSON.stringify(namesAfterRename)}`,
    );
  }
  if (namesAfterRename.includes(fileName)) {
    throw new Error(
      `REST GET /api/files?path=${folderName} still included the old name "${fileName}": ${JSON.stringify(namesAfterRename)}`,
    );
  }
  console.log(`  OK verified rename via REST GET /api/files?path=${folderName}`);

  // --- back to Home, right-click the folder -> Delete -> confirm ----------
  await goHome(page);
  await waitForVisibleText(page, folderName);
  await page.getByText(folderName, { exact: true }).click({ button: 'right' });
  await page.getByTestId('file-action-delete').click();
  await waitForGone(page, folderName);
  console.log(`  OK deleted folder "${folderName}" (right-click -> action sheet -> Delete -> confirm)`);

  // --- verify gone via a raw REST GET on the root --------------------------
  const rootListing = restListFiles('');
  if (entryNames(rootListing).includes(folderName)) {
    throw new Error(`REST GET /api/files (root) still included "${folderName}" after delete`);
  }
  console.log('  OK verified gone via REST GET /api/files (root)');
}

async function main() {
  const startedAt = Date.now();

  // Best-effort pre-clean in case a prior failed run left these behind —
  // makes this script safely re-runnable (same convention as
  // `files_rest_smoke.sh`'s `trap cleanup EXIT`).
  restDeleteBestEffort(ASCII_FLOW.folderName);
  restDeleteBestEffort(UNICODE_FLOW.folderName);

  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    // `Delete` uses `window.confirm` on web (see `confirmDeleteEntry` in
    // `files.tsx`) — Playwright auto-DISMISSES native dialogs unless a
    // handler is registered, so this must be set up before any Delete click.
    page.on('dialog', (dialog) => dialog.accept());

    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
    await page.getByRole('tab', { name: 'Files' }).click();
    await waitForVisibleText(page, 'Home'); // confirms the screen mounted + the root dir loaded

    await runFullFlow(page, ASCII_FLOW);
    await runFullFlow(page, UNICODE_FLOW);

    const elapsedMs = Date.now() - startedAt;
    console.log(`PASS: both flows (ASCII + space/non-ASCII) completed in ${elapsedMs}ms`);
  } finally {
    await browser.close();
    restDeleteBestEffort(ASCII_FLOW.folderName);
    restDeleteBestEffort(UNICODE_FLOW.folderName);
  }
}

main().catch((error) => {
  console.error('FAIL:', error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
