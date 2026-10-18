import { Link } from 'expo-router';
import { View } from 'react-native';

export default function Settings() {
  return (
    <View testID="settings-screen">
      <Link testID="open-advanced" href="/settings/advanced">
        Advanced
      </Link>
    </View>
  );
}
