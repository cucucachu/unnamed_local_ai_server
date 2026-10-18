import { Stack } from 'expo-router';
import { Pressable, Text } from 'react-native';

export default function RootLayout() {
  return (
    <Stack>
      <Stack.Screen name="(tabs)" />
      <Stack.Screen name="item/[id]" options={{ title: 'Item' }} />
      <Stack.Screen
        name="new"
        options={{
          presentation: 'modal',
          title: 'New item',
          headerRight: ({ tintColor }) => (
            <Pressable testID="save">
              <Text style={{ color: tintColor }}>Save</Text>
            </Pressable>
          ),
        }}
      />
    </Stack>
  );
}
