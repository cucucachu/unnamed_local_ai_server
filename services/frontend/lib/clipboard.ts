import * as Clipboard from 'expo-clipboard';

/** Cross-platform clipboard write used by the chat bubble Copy action (M9-01). */
export async function copyToClipboard(text: string): Promise<void> {
  await Clipboard.setStringAsync(text);
}
