import Constants, { ExecutionEnvironment } from 'expo-constants';
import { Platform } from 'react-native';

/**
 * Expo Go cannot talk to Android Keystore / Secure Enclave, so it keeps
 * `X-HomeAI-Client: native` and password login. A prebuild/dev-client
 * (or store build) is the host app and sends `host` for pairing login.
 */

export function isHostApp(): boolean {
  if (Platform.OS === 'web') return false;
  const env = Constants.executionEnvironment;
  return env === ExecutionEnvironment.Bare || env === ExecutionEnvironment.Standalone;
}

export function homeAiClientHeader(): 'native' | 'host' | undefined {
  if (Platform.OS === 'web') return undefined;
  return isHostApp() ? 'host' : 'native';
}
