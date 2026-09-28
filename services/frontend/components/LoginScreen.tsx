import { useEffect, useState } from 'react';

import { AuthField, AuthFormFrame, AuthLink, AuthSubmitButton } from '@/components/AuthForm';
import { useAuth } from '@/components/AuthProvider';
import { ApiError } from '@/lib/api';
import { authErrorMessage, getAuthStatus, loadPairedDeviceId, passkeysAvailable } from '@/lib/auth';
import { isHostApp } from '@/lib/client';
import { passkeysSupported } from '@/lib/webauthn';
import { theme } from '@/lib/theme';

import { Pressable, Platform, StyleSheet, Text } from 'react-native';

/** Username + password, plus a TOTP field once the platform answers
 * `totp_required`. When status says passkeys are available at this origin,
 * also offers passkey sign-in. Public HTTPS (flag on + this request is
 * public) hides the password field on web. The host app (not Expo Go)
 * offers paired-device sign-in. `onShowSetup` is offered while bootstrap
 * is still open. */
export function LoginScreen({ onShowSetup }: { onShowSetup?: () => void }) {
  const { login, loginWithPasskey, loginWithDevice, pairDevice } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [totpCode, setTotpCode] = useState('');
  const [needsTotp, setNeedsTotp] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offerPasskey, setOfferPasskey] = useState(false);
  const [passkeyOnly, setPasskeyOnly] = useState(false);
  const [paired, setPaired] = useState(false);
  const [pairPayload, setPairPayload] = useState('');
  const host = isHostApp();

  useEffect(() => {
    let cancelled = false;
    getAuthStatus()
      .then((status) => {
        if (cancelled) return;
        setOfferPasskey(passkeysSupported() && passkeysAvailable(status.webauthn));
        setPasskeyOnly(
          Platform.OS === 'web' && Boolean(status.public_https) && status.origin === 'public',
        );
      })
      .catch(() => {});
    if (host) {
      loadPairedDeviceId().then((id) => {
        if (!cancelled) setPaired(Boolean(id));
      });
    }
    return () => {
      cancelled = true;
    };
  }, [host]);

  function fail(caught: unknown) {
    if (caught instanceof ApiError && (caught.detail === 'totp_required' || caught.detail === 'invalid_totp')) {
      setNeedsTotp(true);
    }
    setError(authErrorMessage(caught));
    setBusy(false);
  }

  async function handleSubmit() {
    if (busy) return;
    if (passkeyOnly) {
      await handlePasskey();
      return;
    }
    if (!username.trim() || !password) {
      setError('Enter your username and password.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await login({ username: username.trim(), password, totpCode: needsTotp ? totpCode.trim() : undefined });
    } catch (caught) {
      fail(caught);
    }
  }

  async function handlePasskey() {
    if (busy) return;
    if (!username.trim()) {
      setError('Enter your username to sign in with a passkey.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await loginWithPasskey({ username: username.trim(), totpCode: needsTotp ? totpCode.trim() : undefined });
    } catch (caught) {
      fail(caught);
    }
  }

  async function handleDevice() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await loginWithDevice(needsTotp ? totpCode.trim() : undefined);
    } catch (caught) {
      fail(caught);
    }
  }

  async function handlePair() {
    if (busy) return;
    if (!pairPayload.trim()) {
      setError('Paste the pairing QR from Settings on the LAN.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await pairDevice(pairPayload.trim());
    } catch (caught) {
      fail(caught);
    }
  }

  return (
    <AuthFormFrame
      title="Sign in"
      subtitle={
        passkeyOnly
          ? 'This server requires a passkey on the public internet.'
          : host
            ? 'Sign in with this paired phone, or use your password on the LAN.'
            : 'Sign in to your Home AI account.'
      }
      error={error}
      testID="auth-login-screen"
      footer={
        onShowSetup ? (
          <AuthLink label="First time here? Set up this server" onPress={onShowSetup} testID="auth-show-setup" />
        ) : null
      }
    >
      {host && paired ? (
        <Pressable
          onPress={handleDevice}
          disabled={busy}
          accessibilityRole="button"
          testID="host-pair-sign-in"
          style={styles.device}
        >
          <Text style={styles.deviceText}>Sign in with this device</Text>
        </Pressable>
      ) : null}
      {host && !paired ? (
        <>
          <AuthField
            label="Pairing code"
            value={pairPayload}
            onChangeText={setPairPayload}
            autoCapitalize="none"
            multiline
            testID="host-pair-payload"
          />
          <Pressable
            onPress={handlePair}
            disabled={busy}
            accessibilityRole="button"
            testID="host-pair-enroll"
            style={styles.device}
          >
            <Text style={styles.deviceText}>Pair this phone</Text>
          </Pressable>
        </>
      ) : null}
      <AuthField
        label="Username"
        value={username}
        onChangeText={setUsername}
        autoComplete="username"
        textContentType="username"
        returnKeyType="next"
        testID="auth-username"
      />
      {passkeyOnly ? null : (
        <AuthField
          label="Password"
          value={password}
          onChangeText={setPassword}
          secureTextEntry
          autoComplete="current-password"
          textContentType="password"
          returnKeyType="go"
          onSubmitEditing={handleSubmit}
          testID="auth-password"
        />
      )}
      {needsTotp ? (
        <AuthField
          label="Authenticator code"
          value={totpCode}
          onChangeText={setTotpCode}
          keyboardType="number-pad"
          autoComplete="one-time-code"
          textContentType="oneTimeCode"
          maxLength={6}
          returnKeyType="go"
          onSubmitEditing={handleSubmit}
          autoFocus
          testID="auth-totp"
        />
      ) : null}
      <AuthSubmitButton
        label={passkeyOnly ? 'Sign in with a passkey' : 'Sign in'}
        busy={busy}
        onPress={handleSubmit}
      />
      {offerPasskey && !passkeyOnly ? (
        <Pressable
          onPress={handlePasskey}
          disabled={busy}
          accessibilityRole="button"
          testID="auth-passkey-submit"
          style={styles.passkey}
        >
          <Text style={styles.passkeyText}>Sign in with a passkey</Text>
        </Pressable>
      ) : null}
    </AuthFormFrame>
  );
}

const styles = StyleSheet.create({
  passkey: {
    alignItems: 'center',
    paddingVertical: 10,
  },
  passkeyText: {
    color: theme.accent,
    fontSize: 15,
    fontWeight: '600',
  },
  device: {
    alignItems: 'center',
    paddingVertical: 12,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: theme.accent,
  },
  deviceText: {
    color: theme.accent,
    fontSize: 15,
    fontWeight: '600',
  },
});
