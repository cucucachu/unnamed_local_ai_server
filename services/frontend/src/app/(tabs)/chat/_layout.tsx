import { Stack } from 'expo-router';

import { theme } from '@/lib/theme';

/**
 * The Chat tab's stack (M3-04). Since M19-02 `index` is the only real
 * screen: it shows the current chat, and `[threadId]` is a deep link that
 * switches to a chat and returns here. The outer `chat` `Tabs.Screen` has
 * `headerShown: false` so this stack's header is the only one.
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
      <Stack.Screen name="index" options={{ title: 'Chat' }} />
      <Stack.Screen name="[threadId]" options={{ headerShown: false, animation: 'none' }} />
    </Stack>
  );
}
