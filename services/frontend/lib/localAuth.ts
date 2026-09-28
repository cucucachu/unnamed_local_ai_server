import { Platform } from 'react-native';

/** Biometric (or device-credential) unlock before a Keystore sign. */

export async function confirmDevicePresence(): Promise<boolean> {
  if (Platform.OS === 'web') return false;
  try {
    const LocalAuthentication = await import('expo-local-authentication');
    const enrolled = await LocalAuthentication.hasHardwareAsync();
    if (!enrolled) {
      // No biometric hardware: still allow the Keystore PIN/pattern path.
      return true;
    }
    const result = await LocalAuthentication.authenticateAsync({
      promptMessage: 'Unlock to sign in',
      cancelLabel: 'Cancel',
      disableDeviceFallback: false,
    });
    return result.success === true;
  } catch {
    return false;
  }
}
