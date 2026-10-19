import { Tabs } from 'expo-router';

export default function TabsLayout() {
  return (
    <Tabs>
      <Tabs.Screen name="index" options={{ title: 'Calculator', icon: 'calculator-outline' }} />
      <Tabs.Screen name="units" options={{ title: 'Units', icon: 'swap-horizontal-outline' }} />
      <Tabs.Screen name="tip" options={{ title: 'Tip', icon: 'receipt-outline' }} />
    </Tabs>
  );
}
