import { Text, TextInput, View, useColorScheme } from 'react-native';

export default function Plain() {
  const scheme = useColorScheme();
  return (
    <View testID="plain-screen" style={{ padding: 16, gap: 8 }}>
      <Text testID="plain-text">
        Plain text <Text testID="nested-text">nested</Text>
      </Text>
      <Text testID="red-text" style={{ color: 'rgb(255, 0, 0)' }}>
        Red
      </Text>
      <Text testID="scheme">{scheme}</Text>
      <TextInput testID="plain-input" placeholder="Type" />
    </View>
  );
}
