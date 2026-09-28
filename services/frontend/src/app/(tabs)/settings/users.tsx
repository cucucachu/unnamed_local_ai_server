import { useCallback } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { useAuth } from '@/components/AuthProvider';
import { ActionButton, Badge, Card, LoadState, Segmented, SettingsFrame, settingsStyles } from '@/components/SettingsUI';
import { useStepUp } from '@/components/StepUpProvider';
import { Toast, useToast } from '@/components/Toast';
import type { User } from '@/lib/auth';
import { adminListUsers, adminUpdateUser } from '@/lib/platform';
import { theme } from '@/lib/theme';
import { useAction, useLoad } from '@/lib/useAsync';

/** Settings → Users (admins): every account, with role and disable.
 * Your own row is read-only — demoting or disabling yourself is done by
 * another admin. */
export default function UsersScreen() {
  const { state } = useAuth();
  const me = state.phase === 'ready' ? state.user : null;
  const { withStepUp } = useStepUp();
  const { message: toast, showToast } = useToast();
  const load = useCallback(() => withStepUp(adminListUsers), [withStepUp]);
  const { data: users, error, reload, setData } = useLoad(load);
  const { busyKey, run } = useAction(showToast);

  async function update(user: User, changes: { role?: User['role']; disabled?: boolean }) {
    await run(user.id, async () => {
      const updated = await withStepUp(() => adminUpdateUser(user.id, changes));
      setData((previous) => previous?.map((u) => (u.id === updated.id ? updated : u)) ?? null);
    });
  }

  return (
    <View style={styles.container}>
      <SettingsFrame title="Users" testID="settings-users-screen">
        {users === null ? (
          <LoadState error={error} onRetry={reload} />
        ) : (
          <Card>
            {users.map((user, index) => {
              const self = user.id === me?.id;
              const disabled = user.disabled_at !== null;
              return (
                <View
                  key={user.id}
                  style={[settingsStyles.row, index === 0 && settingsStyles.firstRow, styles.userRow]}
                  testID={`admin-user-row-${user.username}`}
                >
                  <View style={styles.userHeader}>
                    <View style={settingsStyles.rowMain}>
                      <Text style={[settingsStyles.rowTitle, disabled && styles.disabledText]}>
                        {user.display_name}
                        {self ? ' (you)' : ''}
                      </Text>
                      <Text style={settingsStyles.muted}>{user.username}</Text>
                    </View>
                    {user.totp_enabled ? <Badge label="2FA" /> : null}
                    {disabled ? <Badge label="Disabled" tone="danger" testID={`admin-user-disabled-${user.username}`} /> : null}
                    {self ? <Badge label={user.role} tone={user.role === 'admin' ? 'accent' : 'muted'} /> : null}
                  </View>
                  {self ? null : (
                    <View style={settingsStyles.rowActions}>
                      <Segmented<User['role']>
                        value={user.role}
                        onChange={(role) => update(user, { role })}
                        disabled={busyKey !== null}
                        options={[
                          { value: 'member', label: 'Member', testID: `admin-user-role-${user.username}-member` },
                          { value: 'admin', label: 'Admin', testID: `admin-user-role-${user.username}-admin` },
                        ]}
                      />
                      <ActionButton
                        label={disabled ? 'Enable' : 'Disable'}
                        variant={disabled ? 'secondary' : 'danger'}
                        compact
                        onPress={() => update(user, { disabled: !disabled })}
                        busy={busyKey === user.id}
                        disabled={busyKey !== null && busyKey !== user.id}
                        testID={`admin-user-disable-${user.username}`}
                      />
                    </View>
                  )}
                </View>
              );
            })}
          </Card>
        )}
        <Text style={settingsStyles.muted}>
          Disabling someone signs them out everywhere. Re-enabling lets them sign in again.
        </Text>
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
  userRow: {
    flexDirection: 'column',
    alignItems: 'stretch',
    gap: 10,
  },
  userHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  disabledText: {
    color: theme.textMuted,
  },
});
