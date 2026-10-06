import { Stack } from 'expo-router';

import { theme } from '@/lib/theme';

/**
 * `/chat` links (M3-04). Since M19-07 the chat is a page of the home pager
 * (`../index.tsx`): `index` shows that page and `[threadId]` switches to a
 * chat first; neither draws anything.
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
      <Stack.Screen name="index" options={{ headerShown: false, animation: 'none' }} />
      <Stack.Screen name="[threadId]" options={{ headerShown: false, animation: 'none' }} />
    </Stack>
  );
}
