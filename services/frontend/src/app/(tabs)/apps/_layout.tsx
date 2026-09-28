import { Stack } from 'expo-router';

import { theme } from '@/lib/theme';

/** The Apps tab: the installed-apps list, the runner and an app's info
 * (history), a stack like `chat/`
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
      <Stack.Screen name="info/[appId]" options={{ title: 'App info' }} />
    </Stack>
  );
}
