import { Link, router } from 'expo-router';
import { Pressable, Text, View } from 'react-native';

export default function List() {
  return (
    <View testID="list-screen">
      <Link testID="open-item" href={{ pathname: '/item/[id]', params: { id: 7 } }}>
        Item 7
      </Link>
      <Pressable testID="open-new" onPress={() => router.push('/new')}>
        <Text>New</Text>
      </Pressable>
    </View>
  );
}
