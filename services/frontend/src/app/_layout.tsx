import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import * as SystemUI from 'expo-system-ui';
import { useEffect, useMemo } from 'react';
import { GestureHandlerRootView } from 'react-native-gesture-handler';

import { AppKeyboardProvider } from '@/components/AppKeyboardProvider';
import { AuthGate } from '@/components/AuthGate';
import { AuthProvider, useAuth } from '@/components/AuthProvider';
import { SettingsProvider } from '@/components/SettingsProvider';
import { useTheme } from '@/lib/theme';

// The host follows the device's light/dark setting (#464): `useTheme()` for
// its screens, this ThemeProvider for the navigation chrome, the status bar
// and the native root background (app.json's dark `backgroundColor` until
// the first render). AppRunner passes the same scheme to apps.
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
  const theme = useTheme();
  const navigationTheme = useMemo(() => {
    const base = theme.scheme === 'dark' ? DarkTheme : DefaultTheme;
    return {
      ...base,
      colors: { ...base.colors, primary: theme.accent, background: theme.bg, card: theme.bg, text: theme.text, border: theme.border },
    };
  }, [theme]);
  useEffect(() => {
    void SystemUI.setBackgroundColorAsync(theme.bg);
  }, [theme.bg]);
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      {/* M9-04: keyboard-controller's provider (native) / no-op (web).
          Must wrap every screen that uses `AppKeyboardAvoidingView`. */}
      <AppKeyboardProvider>
        <ThemeProvider value={navigationTheme}>
          <AuthProvider>
            <AuthGate>
              <AppStack />
            </AuthGate>
          </AuthProvider>
          <StatusBar style={theme.scheme === 'dark' ? 'light' : 'dark'} />
        </ThemeProvider>
      </AppKeyboardProvider>
    </GestureHandlerRootView>
  );
}

function AppStack() {
  const { state } = useAuth();
  const user = state.phase === 'ready' ? state.user : null;

  return (
    // M8-02: loaded once at the app root (per the ticket) so every screen
    // reads the same in-memory document via `useSettings()` instead of each
    // fetching its own copy. Reloaded when the signed-in user changes.
    <SettingsProvider reloadKey={user?.id ?? null}>
      {/* Guards: Expo Router sends any visit to an unavailable screen to
          the first available one — `(tabs)` when signed in, `login` when
          not. `invite` and `pair` are reachable either way. */}
      <Stack screenOptions={{ headerShown: false }}>
        <Stack.Protected guard={user !== null}>
          <Stack.Screen name="(tabs)" />
          {/* M5-02: the media player opens as a modal over whichever tab
              triggered it (the Files tab today), per the ticket's "Expo
              Router modal presentation" spec — a sibling of the implicit
              `(tabs)` group route, not nested inside it. */}
          <Stack.Screen name="media" options={{ presentation: 'modal', headerShown: false }} />
          {/* M22-10: a text file's viewer and editor, over the Files tab. */}
          <Stack.Screen name="file" options={{ headerShown: false }} />
        </Stack.Protected>
        <Stack.Protected guard={user === null}>
          <Stack.Screen name="login" />
        </Stack.Protected>
        <Stack.Screen name="invite" />
        <Stack.Screen name="pair" />
      </Stack>
    </SettingsProvider>
  );
}
