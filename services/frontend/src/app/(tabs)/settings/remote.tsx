import { useCallback, useState } from 'react';
import { Alert, Platform, StyleSheet, Text, View } from 'react-native';
import QRCode from 'react-native-qrcode-svg';

import { AuthField } from '@/components/AuthForm';
import {
  ActionButton,
  Card,
  ErrorText,
  LoadState,
  SectionTitle,
  SettingsFrame,
  settingsStyles,
} from '@/components/SettingsUI';
import { Toast, useToast } from '@/components/Toast';
import { copyToClipboard } from '@/lib/clipboard';
import {
  createWireGuardDevice,
  listWireGuardDevices,
  platformErrorMessage,
  revokeWireGuardDevice,
  type CreatedWireGuardDevice,
  type WireGuardDevice,
} from '@/lib/platform';
import { relativeTime } from '@/lib/relativeTime';
import { monospaceFontFamily, theme } from '@/lib/theme';
import { useAction, useLoad } from '@/lib/useAsync';

/** Same Alert (native) / window.confirm (web) split as files.tsx: RN's
 * Alert.alert has no web implementation. */
function confirmRevoke(device: WireGuardDevice): Promise<boolean> {
  const message = `Revoke "${device.name}"? That device can no longer use the VPN. Sessions signed in from it are signed out.`;
  if (Platform.OS === 'web') {
    return Promise.resolve(window.confirm(message));
  }
  return new Promise((resolve) => {
    Alert.alert('Revoke device', message, [
      { text: 'Cancel', style: 'cancel', onPress: () => resolve(false) },
      { text: 'Revoke', style: 'destructive', onPress: () => resolve(true) },
    ]);
  });
}

/** Settings → Remote access: WireGuard device profiles (QR once), revoke. */
export default function RemoteAccessScreen() {
  const { message: toast, showToast } = useToast();
  const load = useCallback(() => listWireGuardDevices(), []);
  const { data: devices, error, reload, setData } = useLoad(load);
  const { busyKey, run } = useAction(showToast);
  const [name, setName] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [created, setCreated] = useState<CreatedWireGuardDevice | null>(null);

  async function handleCreate() {
    const trimmed = name.trim();
    if (!trimmed) return;
    setCreating(true);
    setCreateError(null);
    try {
      const device = await createWireGuardDevice(trimmed);
      setCreated(device);
      setName('');
      setData((previous) => (previous ? [...previous, device] : previous));
    } catch (caught) {
      setCreateError(platformErrorMessage(caught));
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(device: WireGuardDevice) {
    if (!(await confirmRevoke(device))) return;
    const ok = await run(device.id, () => revokeWireGuardDevice(device.id));
    if (!ok) return;
    if (created?.id === device.id) setCreated(null);
    setData((previous) => previous?.filter((d) => d.id !== device.id) ?? null);
  }

  return (
    <View style={styles.container}>
      <SettingsFrame title="Remote access" testID="settings-remote-screen">
        <Text style={settingsStyles.muted}>
          Add a phone or laptop while you are on the home Wi-Fi, then scan the QR code in a WireGuard
          app. Revoking a device removes its VPN access. Host setup (kernel module, router UDP 51820,
          firewall) is documented in Networking — this screen does not open the internet by itself.
        </Text>

        <SectionTitle>New device</SectionTitle>
        <Card>
          <View style={settingsStyles.cardBody}>
            <AuthField
              label="Name"
              value={name}
              onChangeText={setName}
              autoCapitalize="words"
              testID="remote-device-name"
            />
            <ErrorText testID="remote-create-error">{createError}</ErrorText>
            <ActionButton
              label="Create"
              variant="primary"
              onPress={handleCreate}
              busy={creating}
              disabled={!name.trim()}
              testID="remote-create"
            />
          </View>
        </Card>

        {created ? (
          <>
            <SectionTitle>Scan this once</SectionTitle>
            <Card>
              <View style={settingsStyles.cardBody}>
                <Text style={settingsStyles.muted}>
                  {created.name} · {created.address}. The private key is shown only this once.
                </Text>
                <View style={styles.qr} testID="remote-created-qr">
                  <QRCode value={created.config} size={200} quietZone={10} />
                </View>
                <Text style={styles.config} selectable testID="remote-created-config">
                  {created.config}
                </Text>
                <ActionButton
                  label="Copy config"
                  onPress={() => {
                    void copyToClipboard(created.config).then(
                      () => showToast('Copied.'),
                      () => showToast("Couldn't copy."),
                    );
                  }}
                  testID="remote-copy-config"
                />
              </View>
            </Card>
          </>
        ) : null}

        <SectionTitle>Devices</SectionTitle>
        {devices === null ? (
          <LoadState error={error} onRetry={reload} />
        ) : devices.length === 0 ? (
          <Text style={settingsStyles.muted} testID="remote-empty">
            No devices yet.
          </Text>
        ) : (
          <Card>
            {devices.map((device, index) => (
              <View
                key={device.id}
                style={[settingsStyles.row, index === 0 && settingsStyles.firstRow]}
                testID={`remote-row-${device.id}`}
              >
                <View style={settingsStyles.rowMain}>
                  <Text style={settingsStyles.rowTitle}>{device.name}</Text>
                  <Text style={settingsStyles.muted}>
                    {device.address} · added {relativeTime(device.created_at)}
                  </Text>
                </View>
                <ActionButton
                  label="Revoke"
                  variant="danger"
                  compact
                  onPress={() => handleRevoke(device)}
                  busy={busyKey === device.id}
                  disabled={busyKey !== null && busyKey !== device.id}
                  testID={`remote-revoke-${device.id}`}
                />
              </View>
            ))}
          </Card>
        )}
      </SettingsFrame>
      <Toast message={toast} testID="settings-toast" />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  qr: {
    alignSelf: 'center',
    backgroundColor: '#fff',
    padding: 12,
    borderRadius: 8,
  },
  config: {
    fontFamily: monospaceFontFamily,
    fontSize: 11,
    color: theme.textMuted,
  },
});
