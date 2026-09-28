import { useEffect, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import QRCode from 'react-native-qrcode-svg';

import { AuthField } from '@/components/AuthForm';
import { useAuth } from '@/components/AuthProvider';
import {
  ActionButton,
  Badge,
  Card,
  ErrorText,
  SectionTitle,
  SettingsFrame,
  settingsStyles,
} from '@/components/SettingsUI';
import {
  confirmTotp,
  disableTotp,
  enrollTotp,
  beginPasskeyRegister,
  finishPasskeyRegister,
  listPasskeys,
  platformErrorMessage,
  revokePasskey,
  updateMe,
  type Passkey,
  type TotpEnrollment,
} from '@/lib/platform';
import { getAuthStatus, passkeysAvailable, type User } from '@/lib/auth';
import { createCredential, passkeysSupported } from '@/lib/webauthn';
import { monospaceFontFamily, theme } from '@/lib/theme';

/** Settings → Account: display name, password, and TOTP enrollment. */
export default function AccountScreen() {
  const { state, updateUser } = useAuth();
  const user = state.phase === 'ready' ? state.user : null;
  if (user === null) return null;

  return (
    <SettingsFrame title="Account" testID="settings-account-screen">
      <SectionTitle>Profile</SectionTitle>
      <ProfileCard displayName={user.display_name} username={user.username} onSaved={updateUser} />
      <SectionTitle>Password</SectionTitle>
      <PasswordCard />
      <SectionTitle>Two-factor authentication</SectionTitle>
      <TotpCard enabled={user.totp_enabled} onChanged={updateUser} />
      <PasskeyCard />
    </SettingsFrame>
  );
}

function ProfileCard({
  displayName,
  username,
  onSaved,
}: {
  displayName: string;
  username: string;
  onSaved: (user: User) => void;
}) {
  const [name, setName] = useState(displayName);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const unchanged = name.trim() === displayName;

  async function handleSave() {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      onSaved(await updateMe({ display_name: name.trim() }));
      setSaved(true);
    } catch (caught) {
      setError(platformErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <View style={settingsStyles.cardBody}>
        <Text style={settingsStyles.muted}>
          Username: <Text style={styles.strong}>{username}</Text>
        </Text>
        <AuthField
          label="Display name"
          value={name}
          onChangeText={(text) => {
            setName(text);
            setSaved(false);
          }}
          autoCapitalize="words"
          maxLength={64}
          testID="account-display-name"
        />
        <ErrorText testID="account-profile-error">{error}</ErrorText>
        {saved ? <Text style={styles.success}>Saved.</Text> : null}
        <ActionButton
          label="Save"
          variant="primary"
          onPress={handleSave}
          busy={busy}
          disabled={unchanged || name.trim().length === 0}
          testID="account-display-name-save"
        />
      </View>
    </Card>
  );
}

function PasswordCard() {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function handleChange() {
    setError(null);
    setDone(false);
    if (next !== confirm) {
      setError("The new passwords don't match.");
      return;
    }
    setBusy(true);
    try {
      await updateMe({ password: next, current_password: current });
      setCurrent('');
      setNext('');
      setConfirm('');
      setDone(true);
    } catch (caught) {
      setError(platformErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <View style={settingsStyles.cardBody}>
        <AuthField
          label="Current password"
          value={current}
          onChangeText={setCurrent}
          secureTextEntry
          autoComplete="current-password"
          textContentType="password"
          testID="account-password-current"
        />
        <AuthField
          label="New password"
          value={next}
          onChangeText={setNext}
          secureTextEntry
          autoComplete="new-password"
          textContentType="newPassword"
          testID="account-password-new"
        />
        <AuthField
          label="Confirm new password"
          value={confirm}
          onChangeText={setConfirm}
          secureTextEntry
          autoComplete="new-password"
          textContentType="newPassword"
          testID="account-password-confirm"
        />
        <ErrorText testID="account-password-error">{error}</ErrorText>
        {done ? (
          <Text style={styles.success} testID="account-password-done">
            Password changed. Your other devices were signed out.
          </Text>
        ) : null}
        <ActionButton
          label="Change password"
          variant="primary"
          onPress={handleChange}
          busy={busy}
          disabled={!current || !next || !confirm}
          testID="account-password-save"
        />
      </View>
    </Card>
  );
}

type TotpStep =
  | { kind: 'idle' }
  | { kind: 'password'; purpose: 'enable' | 'disable' }
  | { kind: 'confirm'; enrollment: TotpEnrollment };

function TotpCard({
  enabled,
  onChanged,
}: {
  enabled: boolean;
  onChanged: (user: User) => void;
}) {
  const [step, setStep] = useState<TotpStep>({ kind: 'idle' });
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function goTo(next: TotpStep) {
    setStep(next);
    setPassword('');
    setCode('');
    setError(null);
  }

  async function attempt(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (caught) {
      setError(platformErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const handlePassword = (purpose: 'enable' | 'disable') =>
    attempt(async () => {
      if (purpose === 'enable') {
        goTo({ kind: 'confirm', enrollment: await enrollTotp(password) });
      } else {
        onChanged(await disableTotp(password));
        goTo({ kind: 'idle' });
      }
    });

  const handleConfirm = () =>
    attempt(async () => {
      onChanged(await confirmTotp(code.trim()));
      goTo({ kind: 'idle' });
    });

  return (
    <Card testID="account-totp">
      <View style={settingsStyles.cardBody}>
        <View style={styles.statusRow}>
          <Text style={settingsStyles.rowTitle}>Authenticator app</Text>
          <Badge label={enabled ? 'On' : 'Off'} tone={enabled ? 'accent' : 'muted'} testID="account-totp-status" />
        </View>
        <Text style={settingsStyles.muted}>
          {enabled
            ? 'Signing in asks for a 6-digit code from your authenticator app.'
            : 'Require a 6-digit code from an authenticator app when signing in.'}
        </Text>

        {step.kind === 'idle' ? (
          enabled ? (
            <ActionButton
              label="Turn off"
              variant="danger"
              onPress={() => goTo({ kind: 'password', purpose: 'disable' })}
              testID="account-totp-disable"
            />
          ) : (
            <ActionButton
              label="Set up"
              variant="primary"
              onPress={() => goTo({ kind: 'password', purpose: 'enable' })}
              testID="account-totp-enable"
            />
          )
        ) : null}

        {step.kind === 'password' ? (
          <>
            <AuthField
              label="Your password"
              value={password}
              onChangeText={setPassword}
              secureTextEntry
              autoFocus
              autoComplete="current-password"
              textContentType="password"
              onSubmitEditing={() => password && handlePassword(step.purpose)}
              testID="account-totp-password"
            />
            <ErrorText testID="account-totp-error">{error}</ErrorText>
            <View style={settingsStyles.rowActions}>
              <ActionButton label="Cancel" onPress={() => goTo({ kind: 'idle' })} testID="account-totp-cancel" />
              <ActionButton
                label={step.purpose === 'enable' ? 'Continue' : 'Turn off'}
                variant={step.purpose === 'enable' ? 'primary' : 'danger'}
                onPress={() => handlePassword(step.purpose)}
                busy={busy}
                disabled={!password}
                testID="account-totp-continue"
              />
            </View>
          </>
        ) : null}

        {step.kind === 'confirm' ? (
          <>
            <Text style={settingsStyles.muted}>
              Scan this code with your authenticator app, or enter the key by hand. Then type the 6-digit code it
              shows.
            </Text>
            <View style={styles.qr} testID="account-totp-qr">
              <QRCode value={step.enrollment.otpauth_uri} size={180} quietZone={10} />
            </View>
            <Text style={settingsStyles.muted}>Setup key</Text>
            <Text style={styles.secret} selectable testID="account-totp-secret">
              {step.enrollment.secret}
            </Text>
            <Text style={styles.uri} selectable numberOfLines={2} testID="account-totp-uri">
              {step.enrollment.otpauth_uri}
            </Text>
            <AuthField
              label="6-digit code"
              value={code}
              onChangeText={setCode}
              keyboardType="number-pad"
              autoComplete="one-time-code"
              textContentType="oneTimeCode"
              maxLength={6}
              onSubmitEditing={() => code.trim().length === 6 && handleConfirm()}
              testID="account-totp-code"
            />
            <ErrorText testID="account-totp-error">{error}</ErrorText>
            <View style={settingsStyles.rowActions}>
              <ActionButton label="Cancel" onPress={() => goTo({ kind: 'idle' })} testID="account-totp-cancel" />
              <ActionButton
                label="Turn on"
                variant="primary"
                onPress={handleConfirm}
                busy={busy}
                disabled={code.trim().length !== 6}
                testID="account-totp-confirm"
              />
            </View>
          </>
        ) : null}
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  strong: {
    color: theme.text,
    fontWeight: '600',
  },
  success: {
    color: theme.success,
    fontSize: 13,
  },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  qr: {
    alignSelf: 'center',
    padding: 4,
    backgroundColor: '#ffffff',
    borderRadius: 8,
  },
  secret: {
    color: theme.text,
    fontFamily: monospaceFontFamily,
    fontSize: 15,
    letterSpacing: 1,
  },
  uri: {
    color: theme.textMuted,
    fontFamily: monospaceFontFamily,
    fontSize: 11,
  },
  passkeyRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
});

function PasskeyCard() {
  const [available, setAvailable] = useState(false);
  const [passkeys, setPasskeys] = useState<Passkey[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await getAuthStatus();
        if (cancelled) return;
        const on = passkeysSupported() && passkeysAvailable(status.webauthn);
        setAvailable(on);
        if (on) setPasskeys(await listPasskeys());
      } catch {
        if (!cancelled) setAvailable(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!available) return null;

  async function handleAdd() {
    setBusy(true);
    setError(null);
    try {
      const options = await beginPasskeyRegister();
      const credential = await createCredential(options);
      const created = await finishPasskeyRegister(credential, 'Browser');
      setPasskeys((previous) => [...(previous ?? []), created]);
    } catch (caught) {
      setError(platformErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleRevoke(id: string) {
    setBusy(true);
    setError(null);
    try {
      await revokePasskey(id);
      setPasskeys((previous) => previous?.filter((item) => item.id !== id) ?? null);
    } catch (caught) {
      setError(platformErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <SectionTitle>Passkeys</SectionTitle>
      <Card testID="account-passkey">
        <View style={settingsStyles.cardBody}>
          <Text style={settingsStyles.muted}>
            Sign in and confirm admin changes with this device instead of a password.
          </Text>
          {(passkeys ?? []).map((item) => (
            <View key={item.id} style={styles.passkeyRow} testID={`account-passkey-row-${item.id}`}>
              <Text style={settingsStyles.rowTitle}>{item.name || 'Passkey'}</Text>
              <ActionButton
                label="Revoke"
                variant="danger"
                compact
                onPress={() => handleRevoke(item.id)}
                busy={busy}
                disabled={busy}
                testID={`account-passkey-revoke-${item.id}`}
              />
            </View>
          ))}
          <ErrorText testID="account-passkey-error">{error}</ErrorText>
          <ActionButton
            label="Add a passkey"
            variant="primary"
            onPress={handleAdd}
            busy={busy}
            disabled={busy}
            testID="account-passkey-add"
          />
        </View>
      </Card>
    </>
  );
}
