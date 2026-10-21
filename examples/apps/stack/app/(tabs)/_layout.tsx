import { Tabs } from 'expo-router';

export default function TabsLayout() {
  return (
    <Tabs>
      <Tabs.Screen name="index" options={{ title: 'Play', icon: 'cube-outline' }} />
      <Tabs.Screen name="scores" options={{ title: 'Scores', icon: 'podium-outline' }} />
    </Tabs>
  );
}
