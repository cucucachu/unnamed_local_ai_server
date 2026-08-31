import Ionicons from '@expo/vector-icons/Ionicons';
import { Tabs } from 'expo-router';

export default function TabsLayout() {
  return (
    <Tabs>
      {/* Hidden redirect-only route so `/` resolves to the `chat` tab — see
          src/app/(tabs)/index.tsx. */}
      <Tabs.Screen name="index" options={{ href: null }} />
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
    </Tabs>
  );
}
