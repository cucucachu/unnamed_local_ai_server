// M19-01: the tab bar is just Chat and Apps; Files, Routines and Settings
// open from their tiles on Apps.

const TIMEOUT_MS = 30_000;

/** Apps tab -> the system app's tile (`files`, `routines`, `settings`). */
export async function openSystemApp(page, slug) {
  await page.getByRole('tab', { name: 'Apps' }).click();
  await page.getByTestId('home-launcher').waitFor({ timeout: TIMEOUT_MS });
  await page.getByTestId(`home-open-${slug}`).click();
}
