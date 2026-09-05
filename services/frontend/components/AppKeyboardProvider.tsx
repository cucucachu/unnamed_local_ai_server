import { type ReactNode } from 'react';
import { KeyboardProvider } from 'react-native-keyboard-controller';

/**
 * M9-04: native half of the root keyboard-controller wrap. Expo 57's
 * edge-to-edge default means Android no longer resizes the window when the
 * keyboard opens, so RN's `KeyboardAvoidingView` with `behavior={undefined}`
 * leaves the composer covered. `KeyboardProvider` is what the library's
 * avoiding/sticky views read events from — it has to sit above every
 * screen that uses them (chat, PromptModal). Web is
 * `AppKeyboardProvider.web.tsx` (a passthrough) so the native module is
 * never bundled into the Caddy export.
 */
export function AppKeyboardProvider({ children }: { children: ReactNode }) {
  return <KeyboardProvider>{children}</KeyboardProvider>;
}
