import { useState } from 'react';

import { AuthField, AuthFormFrame, AuthLink, AuthSubmitButton } from '@/components/AuthForm';
import { useAuth } from '@/components/AuthProvider';
import { NewAccountFields, useNewAccountForm } from '@/components/NewAccountFields';
import { ApiError } from '@/lib/api';
import { authErrorMessage } from '@/lib/auth';

/** First-run bootstrap: the one-time setup code (from the platform's logs
 * or `/data/platform/setup-code`) plus the first admin account. Accounts
 * made with the recovery CLI don't close setup, so `onShowLogin` lets them
 * sign in meanwhile. */
export function SetupScreen({ onShowLogin }: { onShowLogin: () => void }) {
  const { setup, refresh } = useAuth();
  const { account, fields, validate } = useNewAccountForm();
  const [setupCode, setSetupCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    if (busy) return;
    const problem = setupCode.trim() ? validate() : 'Enter the setup code.';
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await setup({ setupCode: setupCode.trim(), ...account });
    } catch (caught) {
      setBusy(false);
      if (caught instanceof ApiError && caught.detail === 'setup_complete') {
        await refresh();
        return;
      }
      setError(authErrorMessage(caught));
    }
  }

  return (
    <AuthFormFrame
      title="Set up Home AI"
      subtitle="Enter the setup code shown in the server logs, then create the administrator account."
      error={error}
      testID="auth-setup-screen"
      footer={<AuthLink label="Already have an account? Sign in" onPress={onShowLogin} testID="auth-show-login" />}
    >
      <AuthField
        label="Setup code"
        value={setupCode}
        onChangeText={setSetupCode}
        autoCapitalize="characters"
        placeholder="XXXX-XXXX-XXXX-XXXX"
        testID="auth-setup-code"
      />
      <NewAccountFields fields={fields} onSubmit={handleSubmit} />
      <AuthSubmitButton label="Create administrator" busy={busy} onPress={handleSubmit} />
    </AuthFormFrame>
  );
}
