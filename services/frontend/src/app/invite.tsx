import { useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';

import { AuthFormFrame, AuthSubmitButton } from '@/components/AuthForm';
import { useAuth } from '@/components/AuthProvider';
import { NewAccountFields, useNewAccountForm } from '@/components/NewAccountFields';
import { authErrorMessage } from '@/lib/auth';

/**
 * Invite accept: `/invite?token=hi_…` on web, `homeai://invite?token=hi_…`
 * on native (the `homeai` scheme in app.json maps onto this route).
 * `AuthGate` lets this route through without a session; accepting creates
 * a member account and signs in as it.
 */
export default function InviteScreen() {
  const router = useRouter();
  const { token } = useLocalSearchParams<{ token?: string }>();
  const { acceptInvite } = useAuth();
  const { account, fields, validate } = useNewAccountForm();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(
    token ? null : 'This invite link is missing its token. Open the full link you were sent.',
  );

  async function handleSubmit() {
    if (busy || !token) return;
    const problem = validate();
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await acceptInvite({ token, ...account });
      router.replace('/');
    } catch (caught) {
      setError(authErrorMessage(caught));
      setBusy(false);
    }
  }

  return (
    <AuthFormFrame
      title="Join Home AI"
      subtitle="You've been invited. Choose a username and password for your account."
      error={error}
      testID="auth-invite-screen"
    >
      <NewAccountFields fields={fields} onSubmit={handleSubmit} />
      <AuthSubmitButton label="Create account" busy={busy} onPress={handleSubmit} />
    </AuthFormFrame>
  );
}
