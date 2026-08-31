import { StyleSheet, Text, View } from 'react-native';

// Placeholder only — the real chat UI lands in M2-06.
export default function ChatScreen() {
  return (
    <View style={styles.container}>
      <Text style={styles.text}>Chat — coming M2-06</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#000000',
  },
  text: {
    color: '#ffffff',
    fontSize: 16,
  },
});
