import { Stack } from 'expo-router';

import { theme } from '@/lib/theme';

/** The Apps tab: the installed-apps list and the runner, a stack like `chat/`
 * (so the outer tab header is off in `../_layout.tsx`). */
export default function AppsStackLayout() {
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: theme.bg },
        headerTintColor: theme.text,
        headerShadowVisible: false,
      }}
    >
      <Stack.Screen name="index" options={{ title: 'Apps' }} />
      <Stack.Screen name="[instanceId]" options={{ title: 'App' }} />
    </Stack>
  );
}
