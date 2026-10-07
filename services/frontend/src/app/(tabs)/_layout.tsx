import Ionicons from '@expo/vector-icons/Ionicons';
import { Stack, useRouter } from 'expo-router';
import { Pressable, StyleSheet } from 'react-native';

import { theme } from '@/lib/theme';

/** Back to the home pager from a screen that uses this stack's own header (Files). */
function BackHome() {
  const router = useRouter();
  return (
    <Pressable
      onPress={() => router.dismissTo('/')}
      style={styles.back}
      accessibilityRole="button"
      accessibilityLabel="Back"
      testID="back-to-apps"
    >
      <Ionicons name="chevron-back" size={26} color={theme.text} />
    </Pressable>
  );
}

/** A deep link or reload anywhere still has the home pager underneath, so back returns to it. */
export const unstable_settings = { initialRouteName: 'index' };

/**
 * The signed-in shell (M19-07; the folder name is from when it was a tab
 * bar). `index` is the home pager: Chat, then a page per space. Everything
 * else is pushed over it and back returns to it: the apps stack (runner,
 * catalog, App info), Files, Routines and Settings, each keeping its own
 * stack. `chat/` and `apps/index` are links to the pager's pages.
 */
export default function ShellLayout() {
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: theme.bg },
        headerTintColor: theme.text,
        headerShadowVisible: false,
      }}
    >
      <Stack.Screen name="index" options={{ title: 'Chat' }} />
      <Stack.Screen name="chat" options={{ headerShown: false, animation: 'none' }} />
      <Stack.Screen name="apps" options={{ headerShown: false }} />
      <Stack.Screen name="files" options={{ title: 'Files', headerLeft: () => <BackHome /> }} />
      {/* M17-09 */}
      <Stack.Screen name="routines" options={{ headerShown: false }} />
      {/* The Settings stack (`settings/_layout.tsx`) draws its own headers. */}
      <Stack.Screen name="settings" options={{ headerShown: false }} />
    </Stack>
  );
}

const styles = StyleSheet.create({
  back: {
    paddingHorizontal: 12,
    paddingVertical: 4,
  },
});
