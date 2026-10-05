import { useCallback, useState } from 'react';
import { Alert, Platform, StyleSheet, Switch, Text, View } from 'react-native';
import QRCode from 'react-native-qrcode-svg';

import { AuthField } from '@/components/AuthForm';
import { useAuth } from '@/components/AuthProvider';
import {
  ActionButton,
  Card,
  ErrorText,
  LoadState,
  SectionTitle,
  SettingsFrame,
  settingsStyles,
} from '@/components/SettingsUI';
import { useStepUp } from '@/components/StepUpProvider';
import { Toast, useToast } from '@/components/Toast';
import { copyToClipboard } from '@/lib/clipboard';
import {
  beginHostPair,
  createWireGuardDevice,
  getPlatformSettings,
  listHostDevices,
  listWireGuardDevices,
  pairingQrValue,
  patchPlatformSettings,
  platformErrorMessage,
  revokeHostDevice,
  revokeWireGuardDevice,
  type CreatedWireGuardDevice,
  type HostDevice,
  type HostPairBegin,
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

function confirmRevokeHost(device: HostDevice): Promise<boolean> {
  const message = `Revoke "${device.name}"? That phone can no longer sign in with its pairing key. Sessions from it are signed out.`;
  if (Platform.OS === 'web') {
    return Promise.resolve(window.confirm(message));
  }
  return new Promise((resolve) => {
    Alert.alert('Revoke host device', message, [
      { text: 'Cancel', style: 'cancel', onPress: () => resolve(false) },
      { text: 'Revoke', style: 'destructive', onPress: () => resolve(true) },
    ]);
  });
}

/** Settings → Remote access: WireGuard device profiles (QR once), host-app
 * pairing QR (LAN), revoke. Admins also get the public HTTPS toggle
 * (M15-05) — it does not punch the host firewall. */
export default function RemoteAccessScreen() {
  const { state: authState } = useAuth();
  const user = authState.phase === 'ready' ? authState.user : null;
  const isAdmin = user?.role === 'admin';
  const { withStepUp } = useStepUp();
  const { message: toast, showToast } = useToast();
  const load = useCallback(() => listWireGuardDevices(), []);
  const { data: devices, error, reload, setData } = useLoad(load);
  const loadHost = useCallback(() => listHostDevices(), []);
  const {
    data: hostDevices,
    error: hostError,
    reload: reloadHost,
    setData: setHostDevices,
  } = useLoad(loadHost);
  const loadSettings = useCallback(
    () => (isAdmin ? getPlatformSettings() : Promise.resolve(null)),
    [isAdmin],
  );
  const {
    data: platformSettings,
    error: settingsError,
    reload: reloadSettings,
    setData: setPlatformSettings,
  } = useLoad(loadSettings);
  const { busyKey, run } = useAction(showToast);
  const [name, setName] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [created, setCreated] = useState<CreatedWireGuardDevice | null>(null);
  const [pair, setPair] = useState<HostPairBegin | null>(null);
  const [pairBusy, setPairBusy] = useState(false);
  const [pairError, setPairError] = useState<string | null>(null);

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

  async function handleShowPair() {
    setPairBusy(true);
    setPairError(null);
    try {
      setPair(await beginHostPair());
    } catch (caught) {
      setPairError(platformErrorMessage(caught));
    } finally {
      setPairBusy(false);
    }
  }

  async function handleRevokeHost(device: HostDevice) {
    if (!(await confirmRevokeHost(device))) return;
    const ok = await run(`host-${device.id}`, () => revokeHostDevice(device.id));
    if (!ok) return;
    setHostDevices((previous) => previous?.filter((d) => d.id !== device.id) ?? null);
  }

  async function handlePublicHttps(value: boolean) {
    const updated = await run('public-https', () => withStepUp(() => patchPlatformSettings(value)));
    if (!updated) return;
    setPlatformSettings((previous) =>
      previous ? { ...previous, public_https: value } : previous,
    );
  }

  const domainOk = platformSettings?.domain_configured === true;

  return (
    <View style={styles.container}>
      <SettingsFrame title="Remote access" testID="settings-remote-screen">
        <Text style={settingsStyles.muted}>
          Add a phone or laptop while you are on the home Wi-Fi, then scan the QR code in a WireGuard
          app. Pair the Home AI host app with a different QR (not a VPN config). Revoking a device
          removes its VPN or pairing access. Host setup (kernel module, router UDP 51820, firewall)
          is documented in Networking — this screen does not open the internet by itself.
        </Text>

        {isAdmin ? (
          <>
            <SectionTitle>Public HTTPS</SectionTitle>
            {platformSettings === null ? (
              <LoadState error={settingsError} onRetry={reloadSettings} />
            ) : (
              <Card testID="settings-public-https-row">
                <View style={[settingsStyles.row, settingsStyles.firstRow]}>
                  <View style={settingsStyles.rowMain}>
                    <Text style={settingsStyles.rowTitle}>Allow public HTTPS</Text>
                    <Text style={settingsStyles.muted} testID="settings-public-https-help">
                      {domainOk
                        ? 'Passkey-only login from the public internet, with tighter rate limits. This does not open the host firewall — human WAN steps are in Networking.'
                        : 'Needs a domain (HOMEAI_DOMAIN or WEBAUTHN_RP_ID) before this can be turned on. This does not open the host firewall.'}
                    </Text>
                  </View>
                  <Switch
                    value={platformSettings.public_https}
                    onValueChange={handlePublicHttps}
                    disabled={!domainOk && !platformSettings.public_https}
                    testID="settings-public-https-switch"
                  />
                </View>
              </Card>
            )}
          </>
        ) : null}

        <SectionTitle>Pair a phone</SectionTitle>
        <Text style={settingsStyles.muted}>
          Show a pairing QR on the LAN, then scan it with the phone&apos;s camera (it opens the Home AI
          host app, not Expo Go) or with Scan pairing QR in the app. Members can pair their own
          device. This is not a WireGuard config.
        </Text>
        <Card>
          <View style={settingsStyles.cardBody}>
            <ErrorText testID="host-pair-error">{pairError}</ErrorText>
            <ActionButton
              label="Show pairing QR"
              variant="primary"
              onPress={handleShowPair}
              busy={pairBusy}
              testID="host-pair-show"
            />
          </View>
        </Card>
        {pair ? (
          <Card>
            <View style={settingsStyles.cardBody}>
              <Text style={settingsStyles.muted} testID="host-pair-user">
                Pair {pair.user} · expires in a few minutes · single use
              </Text>
              <View style={styles.qr} testID="host-pair-qr">
                <QRCode value={pairingQrValue(pair)} size={200} quietZone={10} />
              </View>
            </View>
          </Card>
        ) : null}
        {hostDevices === null ? (
          <LoadState error={hostError} onRetry={reloadHost} />
        ) : hostDevices.length === 0 ? (
          <Text style={settingsStyles.muted} testID="host-pair-empty">
            No host apps paired yet.
          </Text>
        ) : (
          <Card>
            {hostDevices.map((device, index) => (
              <View
                key={device.id}
                style={[settingsStyles.row, index === 0 && settingsStyles.firstRow]}
                testID={`host-pair-row-${device.id}`}
              >
                <View style={settingsStyles.rowMain}>
                  <Text style={settingsStyles.rowTitle}>{device.name}</Text>
                  <Text style={settingsStyles.muted}>
                    added {relativeTime(device.created_at)}
                    {device.last_used_at ? ` · last used ${relativeTime(device.last_used_at)}` : ''}
                  </Text>
                </View>
                <ActionButton
                  label="Revoke"
                  variant="danger"
                  compact
                  onPress={() => handleRevokeHost(device)}
                  busy={busyKey === `host-${device.id}`}
                  disabled={busyKey !== null && busyKey !== `host-${device.id}`}
                  testID={`host-pair-revoke-${device.id}`}
                />
              </View>
            ))}
          </Card>
        )}

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
