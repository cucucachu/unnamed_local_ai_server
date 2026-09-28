/** Web never generates Keystore keys; pairing enrolls a *phone*. */

export async function generateDeviceKey(): Promise<string> {
  throw new Error('device_key_unavailable');
}

export async function signChallenge(_challengeB64url: string): Promise<string> {
  throw new Error('device_key_unavailable');
}

export async function devicePublicKey(): Promise<string | null> {
  return null;
}

export async function hasDeviceKey(): Promise<boolean> {
  return false;
}

export async function deleteDeviceKey(): Promise<void> {}
