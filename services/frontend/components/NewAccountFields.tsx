import { useState } from 'react';

import { AuthField } from '@/components/AuthForm';

export interface NewAccount {
  username: string;
  displayName: string;
  password: string;
}

/** Field state shared by the Setup and Invite screens. `validate()` returns
 * the first client-side problem (so the user doesn't wait on a round trip
 * for a typo), or `null` when the form is ready to submit. */
export function useNewAccountForm() {
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');

  function validate(): string | null {
    if (!username.trim() || !displayName.trim() || !password) return 'Please fill in every field.';
    if (password.length < 8) return 'Passwords must be at least 8 characters.';
    if (password !== confirm) return "Passwords don't match.";
    return null;
  }

  const account: NewAccount = { username: username.trim(), displayName: displayName.trim(), password };
  const fields = { username, setUsername, displayName, setDisplayName, password, setPassword, confirm, setConfirm };
  return { account, fields, validate };
}

export function NewAccountFields({
  fields,
  onSubmit,
}: {
  fields: ReturnType<typeof useNewAccountForm>['fields'];
  onSubmit: () => void;
}) {
  return (
    <>
      <AuthField
        label="Username"
        value={fields.username}
        onChangeText={fields.setUsername}
        autoComplete="username"
        textContentType="username"
        testID="auth-username"
      />
      <AuthField
        label="Display name"
        value={fields.displayName}
        onChangeText={fields.setDisplayName}
        autoCapitalize="words"
        autoComplete="name"
        textContentType="name"
        testID="auth-display-name"
      />
      <AuthField
        label="Password"
        value={fields.password}
        onChangeText={fields.setPassword}
        secureTextEntry
        autoComplete="new-password"
        textContentType="newPassword"
        testID="auth-password"
      />
      <AuthField
        label="Confirm password"
        value={fields.confirm}
        onChangeText={fields.setConfirm}
        secureTextEntry
        autoComplete="new-password"
        textContentType="newPassword"
        returnKeyType="go"
        onSubmitEditing={onSubmit}
        testID="auth-password-confirm"
      />
    </>
  );
}
