import { useRouter } from 'expo-router';
import type { ReactNode } from 'react';
import { useCallback } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Switch, Text, View } from 'react-native';

import { useAuth } from '@/components/AuthProvider';
import { NavRow, SectionTitle, Segmented, SettingsFrame } from '@/components/SettingsUI';
import { useSettings } from '@/components/SettingsProvider';
import { Toast, useToast } from '@/components/Toast';
import { ApiError } from '@/lib/api';
import type { EditModeDefault } from '@/lib/settings';
import { theme } from '@/lib/theme';

/**
 * Settings hub (M8-02, a tab since M14-02). Reachable from the Settings
 * tab, the Home launcher tile, and the chat thread-list gear (see
 * `chat/index.tsx`). M10-07 made it the hub of the Settings stack
 * (`settings/_layout.tsx`): links to Account, Sessions, Spaces, and
 * (admins) Users and Invites, then the chat settings.
 *
 * Every chat control here reads `useSettings()`'s current document and calls
 * its `updateSettings` action on change — that hook already does the
 * optimistic-apply / revert-on-failure dance (`components/
 * SettingsProvider.tsx`); this screen's only job on top of that is
 * surfacing a toast when a change fails, using the exact same `Toast`/
 * `useToast` convention `chat/index.tsx`/`files.tsx` already use for
 * optimistic-revert-on-failure.
 */
export default function SettingsScreen() {
  const router = useRouter();
  const { settings, loading, updateSettings } = useSettings();
  const { state: authState, logout } = useAuth();
  const user = authState.phase === 'ready' ? authState.user : null;
  const { message: toast, showToast } = useToast();

  const handleToggleHitl = useCallback(
    (value: boolean) => {
      updateSettings({ hitl_enabled: value }).catch((error) => {
        showToast(error instanceof ApiError ? error.detail : 'Failed to update settings');
      });
    },
    [updateSettings, showToast],
  );

  const handleToggleThinking = useCallback(
    (value: boolean) => {
      updateSettings({ thinking_enabled: value }).catch((error) => {
        showToast(error instanceof ApiError ? error.detail : 'Failed to update settings');
      });
    },
    [updateSettings, showToast],
  );

  const handleSetEditMode = useCallback(
    (value: EditModeDefault) => {
      updateSettings({ edit_mode_default: value }).catch((error) => {
        showToast(error instanceof ApiError ? error.detail : 'Failed to update settings');
      });
    },
    [updateSettings, showToast],
  );

  return (
    <View style={styles.container}>
      <SettingsFrame
        title="Settings"
        leading="close"
        footer={
          <View style={styles.account}>
            {user ? (
              <Text style={styles.accountText} testID="settings-account">
                Signed in as {user.display_name} ({user.username})
              </Text>
            ) : null}
            {/* Signing out flips `_layout.tsx`'s route guards, which close this
                modal and land on Login. */}
            <Pressable
              onPress={logout}
              style={styles.logoutButton}
              accessibilityRole="button"
              testID="settings-logout"
            >
              <Text style={styles.logoutText}>Log out</Text>
            </Pressable>
          </View>
        }
      >
        <SectionTitle>Account</SectionTitle>
        <NavRow
          icon="person-circle-outline"
          title="Account"
          description="Name, password, two-factor authentication"
          onPress={() => router.push('/settings/account')}
          testID="settings-nav-account"
        />
        <NavRow
          icon="phone-portrait-outline"
          title="Sessions"
          description="Devices signed in to your account"
          onPress={() => router.push('/settings/sessions')}
          testID="settings-nav-sessions"
        />
        <NavRow
          icon="wifi-outline"
          title="Remote access"
          description="WireGuard, pair a phone, public HTTPS"
          onPress={() => router.push('/settings/remote')}
          testID="settings-nav-remote"
        />
        <NavRow
          icon="people-outline"
          title="Spaces"
          description="Your spaces and who can use them"
          onPress={() => router.push('/settings/spaces')}
          testID="settings-nav-spaces"
        />

        {user?.role === 'admin' ? (
          <>
            <SectionTitle>Admin</SectionTitle>
            <NavRow
              icon="shield-checkmark-outline"
              title="Users"
              description="Roles and disabled accounts"
              onPress={() => router.push('/settings/users')}
              testID="settings-nav-users"
            />
            <NavRow
              icon="mail-outline"
              title="Invites"
              description="Invite someone to this server"
              onPress={() => router.push('/settings/invites')}
              testID="settings-nav-invites"
            />
          </>
        ) : null}

        <SectionTitle>Chat</SectionTitle>
        {loading || settings === null ? (
          <View style={styles.centered}>
            <ActivityIndicator size="large" color={theme.accent} />
          </View>
        ) : (
          <View style={styles.chatSettings}>
            <SettingRow
              title="Require approval"
              description="Require approval before the agent writes files or runs code."
              testID="settings-hitl-row"
            >
              <Switch
                value={settings.hitl_enabled}
                onValueChange={handleToggleHitl}
                testID="settings-hitl-switch"
              />
            </SettingRow>

            <SettingRow
              title="Show thinking"
              description="Show the agent's step-by-step reasoning while it works."
              testID="settings-thinking-row"
            >
              <Switch
                value={settings.thinking_enabled}
                onValueChange={handleToggleThinking}
                testID="settings-thinking-switch"
              />
            </SettingRow>

            <SettingRow
              title="Default edit mode"
              description="How file edits are applied by default: replace the file's content, or branch into a new version."
              testID="settings-edit-mode-row"
            >
              <Segmented<EditModeDefault>
                value={settings.edit_mode_default}
                onChange={handleSetEditMode}
                options={[
                  { value: 'truncate', label: 'Replace', testID: 'settings-edit-mode-truncate' },
                  { value: 'fork', label: 'Branch', testID: 'settings-edit-mode-fork' },
                ]}
              />
            </SettingRow>

            <Text style={styles.footnote} testID="settings-voice-footnote">
              Voice input uses your browser&apos;s speech service.
            </Text>
          </View>
        )}
      </SettingsFrame>

      <Toast message={toast} testID="settings-toast" />
    </View>
  );
}

function SettingRow({
  title,
  description,
  testID,
  children,
}: {
  title: string;
  description: string;
  testID: string;
  children: ReactNode;
}) {
  return (
    <View style={styles.row} testID={testID}>
      <View style={styles.rowText}>
        <Text style={styles.rowTitle}>{title}</Text>
        <Text style={styles.rowDescription}>{description}</Text>
      </View>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  centered: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 24,
  },
  chatSettings: {
    gap: 20,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 16,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: theme.border,
  },
  rowText: {
    flex: 1,
    gap: 4,
  },
  rowTitle: {
    color: theme.text,
    fontSize: 16,
    fontWeight: '600',
  },
  rowDescription: {
    color: theme.textMuted,
    fontSize: 13,
  },
  footnote: {
    color: theme.textMuted,
    fontSize: 12,
    lineHeight: 16,
    marginTop: 8,
  },
  account: {
    marginTop: 'auto',
    width: '100%',
    maxWidth: 640,
    alignSelf: 'center',
    padding: 16,
    gap: 12,
    borderTopWidth: 1,
    borderTopColor: theme.border,
  },
  accountText: {
    color: theme.textMuted,
    fontSize: 13,
  },
  logoutButton: {
    alignItems: 'center',
    paddingVertical: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: theme.danger,
  },
  logoutText: {
    color: theme.danger,
    fontSize: 15,
    fontWeight: '600',
  },
});
