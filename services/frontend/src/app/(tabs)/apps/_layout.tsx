import { Stack } from 'expo-router';

import { theme } from '@/lib/theme';

/** Apps (M14-02's Home, renamed in M19-01): launcher, catalog/install/update, the runner, and app
 * info (history). A stack like `chat/` (so the outer tab header is off in
 * `../_layout.tsx`). Routes stay under `/apps` so existing links and e2e
 * keep working. */

/** A deep link or reload on `/apps/<id>` still has the grid underneath, so
 * back and the Apps tab return to it. */
export const unstable_settings = { initialRouteName: 'index' };

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
      <Stack.Screen name="catalog" options={{ title: 'Catalog' }} />
      <Stack.Screen name="install" options={{ title: 'Install' }} />
      <Stack.Screen name="update" options={{ title: 'Update' }} />
      <Stack.Screen name="[instanceId]" options={{ title: 'App' }} />
      <Stack.Screen name="info/[appId]" options={{ title: 'App info' }} />
    </Stack>
  );
}
