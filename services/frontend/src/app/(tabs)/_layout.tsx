import Ionicons from '@expo/vector-icons/Ionicons';
import { Tabs } from 'expo-router';

import { useInboxUnread } from '@/lib/inbox';

export default function TabsLayout() {
  const unread = useInboxUnread();
  return (
    <Tabs>
      {/* Hidden redirect-only route so `/` resolves to Home (`/apps`) —
          see src/app/(tabs)/index.tsx. */}
      <Tabs.Screen name="index" options={{ href: null }} />
      {/* M14-02: Home is the default tab (the former Apps list plus system
          app tiles and a space switcher). Nested stack owns headers. */}
      <Tabs.Screen
        name="apps"
        options={{
          title: 'Home',
          headerShown: false,
          // M17-05: unread routine runs (finished, failed, needing approval).
          tabBarBadge: unread ? unread : undefined,
          tabBarIcon: ({ color, focused, size }) => (
            <Ionicons name={focused ? 'home' : 'home-outline'} color={color} size={size} />
          ),
        }}
      />
      <Tabs.Screen
        name="chat"
        options={{
          title: 'Chat',
          // M3-04: `chat` is now a nested Stack (list + `[threadId]`, see
          // `chat/_layout.tsx`) instead of one flat screen — that inner
          // Stack owns its own per-screen headers now, so the outer tab
          // header is turned off here. Verified this is actually needed
          // (not just cargo-culted): without `headerShown: false`, the tab
          // bar's own "Chat" header rendered ABOVE the stack's own header,
          // stacking two headers — confirmed via a live dev-server render.
          headerShown: false,
          tabBarIcon: ({ color, focused, size }) => (
            <Ionicons name={focused ? 'chatbubbles' : 'chatbubbles-outline'} color={color} size={size} />
          ),
        }}
      />
      <Tabs.Screen
        name="files"
        options={{
          title: 'Files',
          tabBarIcon: ({ color, focused, size }) => (
            <Ionicons name={focused ? 'folder' : 'folder-outline'} color={color} size={size} />
          ),
        }}
      />
      {/* M14-02: Settings is a tab (Home, Chat, Files, Settings). The stack
          that used to be a sibling modal still lives at `/settings`. */}
      <Tabs.Screen
        name="settings"
        options={{
          title: 'Settings',
          headerShown: false,
          tabBarIcon: ({ color, focused, size }) => (
            <Ionicons name={focused ? 'settings' : 'settings-outline'} color={color} size={size} />
          ),
        }}
      />
    </Tabs>
  );
}
