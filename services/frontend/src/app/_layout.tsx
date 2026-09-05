import { DarkTheme, Stack, ThemeProvider } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { GestureHandlerRootView } from 'react-native-gesture-handler';

import { SettingsProvider } from '@/components/SettingsProvider';

// Dark is the forced/default scheme for this app (not system-following) —
// see M2-05 ticket. `userInterfaceStyle: "dark"` in app.json handles the
// native chrome; this ThemeProvider handles the JS-level navigation theme.
//
// `GestureHandlerRootView` wraps the whole app (per
// react-native-gesture-handler's own installation docs — confirmed by
// reading node_modules/react-native-gesture-handler/src/components/
// GestureHandlerRootView.tsx, a thin View that installs the native gesture
// context) because `chat/index.tsx`'s `Swipeable` (M3-04 swipe-to-delete)
// throws `PanGestureHandler must be used as a descendant of
// GestureHandlerRootView` on native without it — and since nothing in this
// app defines an error boundary, that render error was taking down the
// ENTIRE app (React unmounts the whole tree on an uncaught render error),
// not just the chat tab, which is why the files tab looked frozen too. Web
// never hit this because `Swipeable` is skipped there entirely
// (`Platform.OS === 'web'` branch in `chat/index.tsx`'s `ThreadRow`).
export default function RootLayout() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <ThemeProvider value={DarkTheme}>
        {/* M8-02: loaded once at the app root (per the ticket) so every
            screen — today just `settings.tsx`, later M8-03/M8-05/M8-07's
            consumers — reads the same in-memory document via
            `useSettings()` instead of each fetching its own copy. */}
        <SettingsProvider>
          <Stack screenOptions={{ headerShown: false }}>
            {/* M5-02: the media player opens as a modal over whichever tab
                triggered it (the Files tab today), per the ticket's "Expo
                Router modal presentation" spec — a sibling of the implicit
                `(tabs)` group route, not nested inside it. */}
            <Stack.Screen name="media" options={{ presentation: 'modal', headerShown: false }} />
            {/* M8-02: same modal-stack-screen pattern as `media` above, for
                the new settings screen. */}
            <Stack.Screen name="settings" options={{ presentation: 'modal', headerShown: false }} />
          </Stack>
        </SettingsProvider>
        <StatusBar style="light" />
      </ThemeProvider>
    </GestureHandlerRootView>
  );
}
