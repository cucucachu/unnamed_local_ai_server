// M19-01: the tab bar is just Chat and Apps; Files, Routines and Settings
// open from their tiles on Apps.
// M19-02: the Chat tab is one chat; history is a drawer, + starts a new chat.
// M19-04: Apps is an icon grid; long press on an app opens its action sheet.
// M19-05: Files opens at the Apps page's space (Personal unless swiped).
// M19-07: no tab bar. `/` is a pager: Chat, then a page per space; the
// indicator at the bottom (`home-page-chat`, `home-space-<slug>`) jumps
// between them. Files is on every space page, Routines and Settings on
// Personal; New chat is the drawer's bottom button.

const TIMEOUT_MS = 30_000;

/** Back to the home pager (`/`) from wherever the page is. */
export async function goHome(page) {
  if (new URL(page.url()).pathname !== '/') {
    await page.goto(new URL('/', page.url()).toString(), { waitUntil: 'domcontentloaded' });
  }
  await page.getByTestId('home-page-indicator').waitFor({ timeout: TIMEOUT_MS });
}

/** The pager's Chat page. */
export async function openChatPage(page) {
  await goHome(page);
  await page.getByTestId('home-page-chat').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="home-page-chat"]')?.getAttribute('aria-selected') === 'true', null, { timeout: TIMEOUT_MS });
}

/** A space's page (its slug), or Personal (the first one) without one; returns the page. */
export async function openSpacePage(page, slug = null) {
  await goHome(page);
  const dot = slug
    ? page.getByTestId(`home-space-${slug}`)
    : page.getByTestId('home-page-indicator').getByRole('tab').nth(1);
  await dot.waitFor({ timeout: TIMEOUT_MS });
  await dot.click();
  const id = await dot.getAttribute('data-testid');
  await page.waitForFunction((testId) => document.querySelector(`[data-testid="${testId}"]`)?.getAttribute('aria-selected') === 'true', id, { timeout: TIMEOUT_MS });
  const pageSlug = id.replace(/^home-space-/, '');
  const pageLocator = page.getByTestId(`apps-space-${pageSlug}`);
  await page.waitForFunction((testId) => {
    const pager = document.querySelector('[data-testid="home-pages"]');
    const target = document.querySelector(`[data-testid="${testId}"]`);
    if (!pager || !target) return false;
    const a = pager.getBoundingClientRect();
    const b = target.getBoundingClientRect();
    return Math.abs(a.left - b.left) < 2;
  }, `apps-space-${pageSlug}`, { timeout: TIMEOUT_MS });
  return pageLocator;
}

/** Personal's tile for a system app (`files`, `routines`, `settings`). */
export async function openSystemApp(page, slug) {
  const personal = await openSpacePage(page);
  await personal.getByTestId(`home-open-${slug}`).click();
}

/** Long press on an app tile: holds the mouse past the tile's 400 ms
 * `delayLongPress`, then waits for the action sheet. */
export async function openAppSheet(page, tile) {
  await tile.click({ delay: 800 });
  await page.getByTestId('app-sheet').waitFor({ timeout: TIMEOUT_MS });
}

/** Chat page -> the drawer's New chat: an empty new chat (no thread until its first send). */
export async function startNewChat(page) {
  await openChatPage(page);
  await page.getByTestId('chat-history-button').click();
  const button = page.getByTestId('chat-drawer-new-chat');
  await button.waitFor({ state: 'visible', timeout: TIMEOUT_MS });
  await button.click();
  await page.locator('[data-testid="chat-view"][data-thread-id=""]').waitFor({ state: 'attached', timeout: TIMEOUT_MS });
}

/** The open chat's thread id, once its first message has created it. */
export async function currentThreadId(page, timeout = TIMEOUT_MS) {
  const view = page.locator('[data-testid="chat-view"]:not([data-thread-id=""])').first();
  await view.waitFor({ state: 'attached', timeout });
  return view.getAttribute('data-thread-id');
}

/** Opens the history drawer and waits for a chat titled `title` in it
 * (searched for, since the drawer lists only the first five). */
export async function findInChatHistory(page, title, timeout = TIMEOUT_MS) {
  const drawer = page.getByTestId('chat-drawer');
  const open = async () => {
    await page.getByTestId('chat-history-button').click();
    await drawer.waitFor({ state: 'visible', timeout });
    await drawer.getByTestId('chat-drawer-search').fill(title);
  };
  await open();
  const deadline = Date.now() + timeout;
  for (;;) {
    const row = drawer.getByTestId('thread-row').filter({ hasText: title }).first();
    if ((await row.count()) > 0) return row;
    if (Date.now() > deadline) throw new Error(`"${title}" never appeared in the chat history`);
    // The drawer loads the list as it opens; reopen to pick up a late title.
    await page.getByTestId('chat-drawer-close').click();
    await page.waitForTimeout(1_000);
    await open();
  }
}
