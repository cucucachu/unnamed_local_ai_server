import { useCallback } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { useAuth } from '@/components/AuthProvider';
import { ActionButton, Badge, Card, LoadState, SettingsFrame, settingsStyles } from '@/components/SettingsUI';
import { Toast, useToast } from '@/components/Toast';
import { listSessions, revokeSession, type Session } from '@/lib/platform';
import { relativeTime } from '@/lib/relativeTime';
import { theme } from '@/lib/theme';
import { useAction, useLoad } from '@/lib/useAsync';

/** Settings → Sessions: every device signed in to this account, with
 * revoke. Revoking this device's own session signs out here too. */
export default function SessionsScreen() {
  const { logout } = useAuth();
  const { message: toast, showToast } = useToast();
  const load = useCallback(() => listSessions(), []);
  const { data: sessions, error, reload, setData } = useLoad(load);
  const { busyKey, run } = useAction(showToast);

  async function handleRevoke(session: Session) {
    const ok = await run(session.id, () => revokeSession(session.id));
    if (!ok) return;
    if (session.current) {
      await logout();
      return;
    }
    setData((previous) => previous?.filter((s) => s.id !== session.id) ?? null);
  }

  return (
    <View style={styles.container}>
      <SettingsFrame title="Sessions" testID="settings-sessions-screen">
        <Text style={settingsStyles.muted}>
          Devices signed in to your account. Revoke any you don&apos;t recognize. Changing your password signs out
          every device but this one.
        </Text>
        {sessions === null ? (
          <LoadState error={error} onRetry={reload} />
        ) : (
          <Card>
            {sessions.map((session, index) => (
              <View
                key={session.id}
                style={[settingsStyles.row, index === 0 && settingsStyles.firstRow]}
                testID={`session-row-${session.id}`}
              >
                <View style={settingsStyles.rowMain}>
                  <View style={styles.titleRow}>
                    <Text style={settingsStyles.rowTitle}>{session.device_label ?? 'Unknown device'}</Text>
                    {session.current ? <Badge label="This device" tone="accent" testID="session-current" /> : null}
                  </View>
                  <Text style={settingsStyles.muted}>
                    Active {relativeTime(session.last_seen_at)} · signed in {relativeTime(session.created_at)}
                  </Text>
                </View>
                <ActionButton
                  label={session.current ? 'Sign out' : 'Revoke'}
                  variant="danger"
                  compact
                  onPress={() => handleRevoke(session)}
                  busy={busyKey === session.id}
                  disabled={busyKey !== null && busyKey !== session.id}
                  testID={`session-revoke-${session.id}`}
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
  titleRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: 8,
  },
});
