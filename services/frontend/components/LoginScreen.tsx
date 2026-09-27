import { useState } from 'react';

import { AuthField, AuthFormFrame, AuthLink, AuthSubmitButton } from '@/components/AuthForm';
import { useAuth } from '@/components/AuthProvider';
import { ApiError } from '@/lib/api';
import { authErrorMessage } from '@/lib/auth';

/** Username + password, plus a TOTP field once the platform answers
 * `totp_required`. `onShowSetup` is offered while bootstrap is still open. */
export function LoginScreen({ onShowSetup }: { onShowSetup?: () => void }) {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [totpCode, setTotpCode] = useState('');
  const [needsTotp, setNeedsTotp] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    if (busy) return;
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

  return (
    <AuthFormFrame
      title="Sign in"
      subtitle="Sign in to your Home AI account."
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
      <AuthSubmitButton label="Sign in" busy={busy} onPress={handleSubmit} />
    </AuthFormFrame>
  );
}
