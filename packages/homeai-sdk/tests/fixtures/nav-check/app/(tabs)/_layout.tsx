import { Tabs } from 'expo-router';

export default function TabsLayout() {
  return (
    <Tabs>
      <Tabs.Screen name="index" options={{ title: 'List', icon: 'list-outline' }} />
      <Tabs.Screen name="settings" options={{ title: 'Settings', icon: 'settings-outline', tabBarBadge: 3 }} />
    </Tabs>
  );
}
