import { DarkTheme, Stack, ThemeProvider } from 'expo-router';
import { StatusBar } from 'expo-status-bar';

// Dark is the forced/default scheme for this app (not system-following) —
// see M2-05 ticket. `userInterfaceStyle: "dark"` in app.json handles the
// native chrome; this ThemeProvider handles the JS-level navigation theme.
export default function RootLayout() {
  return (
    <ThemeProvider value={DarkTheme}>
      <Stack screenOptions={{ headerShown: false }} />
      <StatusBar style="light" />
    </ThemeProvider>
  );
}
