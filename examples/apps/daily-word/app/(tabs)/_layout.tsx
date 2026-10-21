import { Tabs } from 'expo-router';

export default function TabsLayout() {
  return (
    <Tabs>
      <Tabs.Screen name="index" options={{ title: 'Play', icon: 'grid-outline' }} />
      <Tabs.Screen name="today" options={{ title: 'Today', icon: 'people-outline' }} />
      <Tabs.Screen name="stats" options={{ title: 'Stats', icon: 'stats-chart-outline' }} />
    </Tabs>
  );
}
