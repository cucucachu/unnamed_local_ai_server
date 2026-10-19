import { Tabs } from 'expo-router';
import { useQuery } from '@homeai/sdk';
import { today } from '../../lib/flashcards';

export default function TabsLayout() {
  const { data } = useQuery<{ n: number }>('SELECT count(*) AS n FROM cards WHERE due <= ?', [today()]);
  const due = data?.[0]?.n ?? 0;
  return (
    <Tabs>
      <Tabs.Screen name="index" options={{ title: 'Decks', icon: 'layers-outline', tabBarBadge: due || undefined }} />
      <Tabs.Screen name="stats" options={{ title: 'Progress', icon: 'stats-chart-outline' }} />
    </Tabs>
  );
}
