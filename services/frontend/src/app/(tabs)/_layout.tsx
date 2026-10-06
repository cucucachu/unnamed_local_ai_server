import Ionicons from '@expo/vector-icons/Ionicons';
import { Tabs, useRouter } from 'expo-router';
import { Pressable, StyleSheet } from 'react-native';

import { useChatAttention } from '@/lib/chatAttention';
import { theme } from '@/lib/theme';

/** Back to Apps from a system app that uses the tab navigator's own header (Files). */
function BackToApps() {
  const router = useRouter();
  return (
    <Pressable
      onPress={() => router.navigate('/apps')}
      style={styles.back}
      accessibilityRole="button"
      accessibilityLabel="Back"
      testID="back-to-apps"
    >
      <Ionicons name="chevron-back" size={26} color={theme.text} />
    </Pressable>
  );
}

/**
 * M19-01: the tab bar is just Chat and Apps. Files, Routines and Settings
 * are system apps opened from Apps, kept here as hidden tabs (no tab button)
 * so each keeps its own stack; back returns to Apps. `history` makes the
 * Android back button do the same.
 */
export default function TabsLayout() {
  const attention = useChatAttention();
  return (
    <Tabs backBehavior="history">
      {/* Hidden redirect-only route so `/` resolves to Chat — see
          src/app/(tabs)/index.tsx. */}
      <Tabs.Screen name="index" options={{ href: null }} />
      <Tabs.Screen
        name="chat"
        options={{
          title: 'Chat',
          // M3-04: `chat` is a nested Stack (list + `[threadId]`, see
          // `chat/_layout.tsx`) that owns its own headers, so the tab
          // header is off (otherwise two headers stack).
          headerShown: false,
          // M17-10: chats waiting on an approval or unread.
          tabBarBadge: attention ? attention : undefined,
          tabBarIcon: ({ color, focused, size }) => (
            <Ionicons name={focused ? 'chatbubbles' : 'chatbubbles-outline'} color={color} size={size} />
          ),
        }}
      />
      {/* M14-02's Home launcher, renamed Apps (M19-01): system app tiles,
          installed apps by space, the catalog. Nested stack owns headers. */}
      <Tabs.Screen
        name="apps"
        options={{
          title: 'Apps',
          headerShown: false,
          tabBarIcon: ({ color, focused, size }) => (
            <Ionicons name={focused ? 'apps' : 'apps-outline'} color={color} size={size} />
          ),
        }}
      />
      <Tabs.Screen name="files" options={{ href: null, title: 'Files', headerLeft: () => <BackToApps /> }} />
      {/* M17-09 */}
      <Tabs.Screen name="routines" options={{ href: null, headerShown: false }} />
      {/* The Settings stack (`settings/_layout.tsx`) draws its own headers. */}
      <Tabs.Screen name="settings" options={{ href: null, headerShown: false }} />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  back: {
    paddingHorizontal: 12,
    paddingVertical: 4,
  },
});
