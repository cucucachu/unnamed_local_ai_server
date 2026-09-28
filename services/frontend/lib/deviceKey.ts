import { Platform } from 'react-native';

/**
 * Hardware-backed P-256 signer for the host app.
 *
 * Android: Android Keystore, `EC` / `secp256r1`, `SHA256withECDSA`,
 * `userAuthenticationRequired` (see `modules/homeai-device-key`).
 * iOS: module stub only (M15-06 is Android first).
 * Expo Go / web: no Keystore; callers must not use this path.
 *
 * Public key and signatures are unpadded base64url, matching
 * `app/core/device_pairs.py`. Tests mock this module.
 */

export interface DeviceKeyNative {
  generateKey(): Promise<string>;
  sign(challengeB64url: string): Promise<string>;
  publicKey(): Promise<string | null>;
  hasKey(): Promise<boolean>;
  deleteKey(): Promise<void>;
}

type NativeLoader = () => DeviceKeyNative;

let loadNative: NativeLoader | null = null;

try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { requireNativeModule } = require('expo-modules-core') as {
    requireNativeModule: (name: string) => DeviceKeyNative;
  };
  loadNative = () => requireNativeModule('HomeAIDeviceKey');
} catch {
  loadNative = null;
}

function native(): DeviceKeyNative {
  if (Platform.OS === 'web' || loadNative === null) {
    throw new Error('device_key_unavailable');
  }
  try {
    return loadNative();
  } catch {
    throw new Error('device_key_unavailable');
  }
}

export async function generateDeviceKey(): Promise<string> {
  return native().generateKey();
}

export async function signChallenge(challengeB64url: string): Promise<string> {
  return native().sign(challengeB64url);
}

export async function devicePublicKey(): Promise<string | null> {
  try {
    return await native().publicKey();
  } catch {
    return null;
  }
}

export async function hasDeviceKey(): Promise<boolean> {
  try {
    return await native().hasKey();
  } catch {
    return false;
  }
}

export async function deleteDeviceKey(): Promise<void> {
  try {
    await native().deleteKey();
  } catch {
    // Best-effort local wipe.
  }
}
