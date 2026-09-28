import { Stack } from 'expo-router';

import { theme } from '@/lib/theme';

/** Home (M14-02): launcher, catalog/install/update, the runner, and app
 * info (history). A stack like `chat/` (so the outer tab header is off in
 * `../_layout.tsx`). Routes stay under `/apps` so existing links and e2e
 * keep working. */
export default function AppsStackLayout() {
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: theme.bg },
        headerTintColor: theme.text,
        headerShadowVisible: false,
      }}
    >
      <Stack.Screen name="index" options={{ title: 'Home' }} />
      <Stack.Screen name="catalog" options={{ title: 'Catalog' }} />
      <Stack.Screen name="install" options={{ title: 'Install' }} />
      <Stack.Screen name="update" options={{ title: 'Update' }} />
      <Stack.Screen name="[instanceId]" options={{ title: 'App' }} />
      <Stack.Screen name="info/[appId]" options={{ title: 'App info' }} />
    </Stack>
  );
}
