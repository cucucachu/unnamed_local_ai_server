import { Tabs, useRouter } from 'expo-router';
import { useQuery, useSpace } from '@homeai/sdk';
import { IconButton } from '@homeai/ui';

export default function TabsLayout() {
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const { data } = useQuery<{ n: number }>('SELECT count(*) AS n FROM transactions WHERE category_id IS NULL');
  const uncategorized = data?.[0]?.n ?? 0;
  const add = () =>
    canEdit ? <IconButton icon="add-outline" accessibilityLabel="Add a transaction" onPress={() => router.push('/new')} testID="new-transaction" /> : null;
  return (
    <Tabs>
      <Tabs.Screen name="index" options={{ title: 'Month', icon: 'pie-chart-outline', headerRight: add }} />
      <Tabs.Screen
        name="transactions"
        options={{ title: 'Transactions', icon: 'list-outline', headerRight: add, tabBarBadge: uncategorized || undefined }}
      />
      <Tabs.Screen name="trends" options={{ title: 'Trends', icon: 'bar-chart-outline' }} />
      <Tabs.Screen name="categories" options={{ title: 'Categories', icon: 'pricetags-outline' }} />
    </Tabs>
  );
}
