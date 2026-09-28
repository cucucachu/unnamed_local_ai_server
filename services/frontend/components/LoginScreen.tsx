import { useEffect, useState } from 'react';

import { AuthField, AuthFormFrame, AuthLink, AuthSubmitButton } from '@/components/AuthForm';
import { useAuth } from '@/components/AuthProvider';
import { ApiError } from '@/lib/api';
import { authErrorMessage, getAuthStatus, passkeysAvailable } from '@/lib/auth';
import { passkeysSupported } from '@/lib/webauthn';
import { theme } from '@/lib/theme';

import { Pressable, Platform, StyleSheet, Text } from 'react-native';

/** Username + password, plus a TOTP field once the platform answers
 * `totp_required`. When status says passkeys are available at this origin,
 * also offers passkey sign-in. Public HTTPS (flag on + this request is
 * public) hides the password field on web. `onShowSetup` is offered while
 * bootstrap is still open. */
export function LoginScreen({ onShowSetup }: { onShowSetup?: () => void }) {
  const { login, loginWithPasskey } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [totpCode, setTotpCode] = useState('');
  const [needsTotp, setNeedsTotp] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offerPasskey, setOfferPasskey] = useState(false);
  const [passkeyOnly, setPasskeyOnly] = useState(false);

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
    return () => {
      cancelled = true;
    };
  }, []);

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
      if (caught instanceof ApiError && (caught.detail === 'totp_required' || caught.detail === 'invalid_totp')) {
        setNeedsTotp(true);
      }
      setError(authErrorMessage(caught));
      setBusy(false);
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
      if (caught instanceof ApiError && (caught.detail === 'totp_required' || caught.detail === 'invalid_totp')) {
        setNeedsTotp(true);
      }
      setError(authErrorMessage(caught));
      setBusy(false);
    }
  }

  return (
    <AuthFormFrame
      title="Sign in"
      subtitle={
        passkeyOnly
          ? 'This server requires a passkey on the public internet.'
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
});
