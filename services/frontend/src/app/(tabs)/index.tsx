import { Redirect } from 'expo-router';

// Expo Router requires a literal `index` route to resolve the `/` URL to a
// default tab (see M2-05 ticket report) — without this, hitting `/` 404s
// even though `apps` (Home) is the first visible `Tabs.Screen`. Hidden from
// the tab bar via `href: null` in `_layout.tsx`; this file only ever redirects.
export default function TabsIndex() {
  return <Redirect href="/apps" />;
}
