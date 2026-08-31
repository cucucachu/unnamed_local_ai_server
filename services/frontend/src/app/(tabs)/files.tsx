import { StyleSheet, Text, View } from 'react-native';

// Placeholder only — the real files UI lands in M3-05.
export default function FilesScreen() {
  return (
    <View style={styles.container}>
      <Text style={styles.text}>Files — coming M3-05</Text>
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
