import { Redirect } from 'expo-router';

// Expo Router requires a literal `index` route to resolve the `/` URL to a
// default tab (see M2-05 ticket report). Hidden from the tab bar via
// `href: null` in `_layout.tsx`; this file only ever redirects. M19-01: the
// app opens in Chat.
export default function TabsIndex() {
  return <Redirect href="/chat" />;
}
