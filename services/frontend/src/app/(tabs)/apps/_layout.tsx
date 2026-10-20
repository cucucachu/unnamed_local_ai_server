import { Stack } from 'expo-router';

import { useTheme } from '@/lib/theme';

/** Apps screens pushed over the home pager (M14-02; M19-07): the catalog,
 * install/update, new from a template, the runner and App info. `index` (`/apps`) is a link to
 * the pager's first space page. Routes stay under `/apps` so existing links
 * and e2e keep working. */
export default function AppsStackLayout() {
  const theme = useTheme();
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: theme.bg },
        headerTintColor: theme.text,
        headerShadowVisible: false,
      }}
    >
      <Stack.Screen name="index" options={{ headerShown: false, animation: 'none' }} />
      <Stack.Screen name="catalog" options={{ title: 'Catalog' }} />
      <Stack.Screen name="install" options={{ title: 'Install' }} />
      <Stack.Screen name="update" options={{ title: 'Update' }} />
      <Stack.Screen name="template" options={{ title: 'New app' }} />
      <Stack.Screen name="[instanceId]" options={{ title: 'App' }} />
      <Stack.Screen name="info/[appId]" options={{ title: 'App info' }} />
    </Stack>
  );
}
