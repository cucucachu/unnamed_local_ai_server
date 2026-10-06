// M19-01: the tab bar is just Chat and Apps; Files, Routines and Settings
// open from their tiles on Apps.
// M19-02: the Chat tab is one chat; history is a drawer, + starts a new chat.

const TIMEOUT_MS = 30_000;

/** Apps tab -> the system app's tile (`files`, `routines`, `settings`). */
export async function openSystemApp(page, slug) {
  await page.getByRole('tab', { name: 'Apps' }).click();
  await page.getByTestId('home-launcher').waitFor({ timeout: TIMEOUT_MS });
  await page.getByTestId(`home-open-${slug}`).click();
}

/** Chat tab -> + : an empty new chat (it has no thread until its first send). */
export async function startNewChat(page) {
  await page.getByRole('tab', { name: 'Chat' }).click();
  const button = page.getByTestId('new-chat-header-button');
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

/** Opens the history drawer and waits for a chat titled `title` in it. */
export async function findInChatHistory(page, title, timeout = TIMEOUT_MS) {
  await page.getByTestId('chat-history-button').click();
  const drawer = page.getByTestId('chat-drawer');
  await drawer.waitFor({ state: 'visible', timeout });
  const deadline = Date.now() + timeout;
  for (;;) {
    const row = drawer.getByTestId('thread-row').filter({ hasText: title }).first();
    if ((await row.count()) > 0) return row;
    if (Date.now() > deadline) throw new Error(`"${title}" never appeared in the chat history`);
    // The drawer loads the list as it opens; reopen to pick up a late title.
    await page.getByTestId('chat-drawer-close').click();
    await page.waitForTimeout(1_000);
    await page.getByTestId('chat-history-button').click();
    await drawer.waitFor({ state: 'visible', timeout });
  }
}
