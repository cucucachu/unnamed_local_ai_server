import { Stack, useRouter } from 'expo-router';
import { useSpace } from '@homeai/sdk';
import { IconButton } from '@homeai/ui';

export default function RootLayout() {
  const router = useRouter();
  const space = useSpace();
  return (
    <Stack>
      <Stack.Screen
        name="index"
        options={{
          title: space ? `${space.name} chat` : 'Group chat',
          headerRight: () => <IconButton icon="search-outline" accessibilityLabel="Search messages" onPress={() => router.push('/search')} testID="open-search" />,
        }}
      />
      <Stack.Screen name="search" options={{ title: 'Search chat' }} />
    </Stack>
  );
}
