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
