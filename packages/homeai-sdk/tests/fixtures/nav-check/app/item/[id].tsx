import { useLocalSearchParams } from 'expo-router';
import { Text } from 'react-native';

export default function Item() {
  const { id } = useLocalSearchParams<{ id: string }>();
  return <Text testID="item-screen">Item {id}</Text>;
}
