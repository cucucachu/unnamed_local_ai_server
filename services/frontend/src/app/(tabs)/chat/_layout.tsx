import { Stack } from 'expo-router';

import { theme } from '@/lib/theme';

/**
 * M3-04: the `chat` tab becomes a stack (list -> individual thread) instead
 * of one flat screen. Turning a single-file tab route (`chat.tsx`) into a
 * folder (`chat/`) with this `_layout.tsx` is exactly expo-router's
 * documented "nest a stack inside a tab" convention for `expo-router@57`
 * (confirmed by this file actually building + navigating correctly via
 * `npx expo export --platform web` and a live dev-server click-through —
 * see this ticket's final report) — no different from a top-level
 * `app/_layout.tsx`, just scoped to this one tab's subtree.
 *
 * `headerShown: false` on the OUTER `chat` `Tabs.Screen` (see
 * `../_layout.tsx`) delegates the header entirely to this inner `Stack`
 * (confirmed by testing without that flag first: the tab bar's own
 * `title: 'Chat'` header rendered ABOVE this stack's own per-screen header,
 * i.e. two stacked headers — not a guess).
 */
export default function ChatStackLayout() {
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: theme.bg },
        headerTintColor: theme.text,
        headerShadowVisible: false,
      }}
    >
      <Stack.Screen name="index" options={{ title: 'Chats' }} />
      <Stack.Screen name="[threadId]" options={{ title: 'Chat' }} />
    </Stack>
  );
}
