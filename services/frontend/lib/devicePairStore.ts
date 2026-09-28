import * as SecureStore from 'expo-secure-store';

/** The enrolled host-app device id, used at login as `device_id`. */

const KEY = 'homeai_host_device_id';

export async function loadPairedDeviceId(): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(KEY);
  } catch {
    return null;
  }
}

export async function savePairedDeviceId(id: string): Promise<void> {
  await SecureStore.setItemAsync(KEY, id);
}

export async function clearPairedDeviceId(): Promise<void> {
  try {
    await SecureStore.deleteItemAsync(KEY);
  } catch {
    // Nothing stored.
  }
}
