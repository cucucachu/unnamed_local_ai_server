import Ionicons from '@expo/vector-icons/Ionicons';
import type { ReactNode } from 'react';
import { useEffect, useState } from 'react';
import { AppState, StyleSheet, Text, View } from 'react-native';

import { AuthField } from '@/components/AuthForm';
import { ActionButton, settingsStyles } from '@/components/SettingsUI';
import { oauthHostsLine, type OAuthSummary } from '@/lib/integrations';
import { monospaceFontFamily, theme } from '@/lib/theme';

/** Pieces shared by the Integrations screens (M18-08). */

export function Notice({
  tone = 'warning',
  children,
  testID,
}: {
  tone?: 'warning' | 'info';
  children: ReactNode;
  testID?: string;
}) {
  return (
    <View style={[styles.notice, tone === 'warning' && styles.noticeWarning]} testID={testID}>
      <Ionicons
        name={tone === 'warning' ? 'warning-outline' : 'information-circle-outline'}
        size={18}
        color={tone === 'warning' ? theme.danger : theme.textMuted}
      />
      <View style={styles.noticeBody}>{children}</View>
    </View>
  );
}

export function PlainHttpWarning() {
  return (
    <Notice testID="integration-plain-http">
      <Text style={styles.noticeText}>
        This server uses plain http. The token, and everything the agent sends it, cross your home network
        unencrypted. Use https if the server supports it.
      </Text>
    </Notice>
  );
}

export function ExposedEntitiesNote() {
  return (
    <Notice tone="info" testID="integration-exposed-note">
      <Text style={styles.noticeText}>
        Some servers decide for themselves what the agent can reach. In Home Assistant, only the devices you expose to
        voice assistants (Settings → Voice assistants → Expose) are visible, whatever is set here.
      </Text>
    </Notice>
  );
}

/** Reload when HomeAI comes back to the foreground: after a sign-in in the browser. */
export function useReloadOnReturn(reload: () => void) {
  useEffect(() => {
    const subscription = AppState.addEventListener('change', (next) => {
      if (next === 'active') reload();
    });
    return () => subscription.remove();
  }, [reload]);
}

/** Where an OAuth sign-in happens, and what disconnecting can't undo there. */
export function SignInNote({ oauth, testID }: { oauth: OAuthSummary | null | undefined; testID?: string }) {
  if (!oauth) return null;
  const hosts = oauthHostsLine(oauth);
  return (
    <Text style={settingsStyles.muted} testID={testID}>
      {hosts}
      {hosts ? ' ' : ''}
      {oauth.revocable
        ? 'Disconnecting signs HomeAI out there too.'
        : `Disconnecting can't sign HomeAI out there: also remove HomeAI in your ${oauth.sign_in_host ?? 'account'} settings.`}
    </Text>
  );
}

/** A token field that's never filled from the server: what's typed is sent
 * once, then cleared. */
export function TokenEntry({
  label,
  submitLabel,
  busy,
  onSubmit,
  testID,
}: {
  label: string;
  submitLabel: string;
  busy: boolean;
  onSubmit: (secret: string) => Promise<boolean>;
  testID: string;
}) {
  const [secret, setSecret] = useState('');
  return (
    <View style={styles.tokenEntry}>
      <AuthField
        label={label}
        value={secret}
        onChangeText={setSecret}
        secureTextEntry
        textContentType="password"
        autoComplete="off"
        placeholder="Paste it here"
        testID={`${testID}-input`}
      />
      <ActionButton
        label={submitLabel}
        variant="primary"
        busy={busy}
        disabled={!secret.trim()}
        onPress={async () => {
          if (await onSubmit(secret.trim())) setSecret('');
        }}
        testID={`${testID}-submit`}
      />
    </View>
  );
}

export function connectionNote(checked: boolean, problem: string | null): string {
  if (checked) return 'Connected.';
  return `Saved, but the server couldn't be asked to check it${problem ? ` (${problem})` : ''}. The first use will tell.`;
}

export const integrationStyles = StyleSheet.create({
  toolRow: {
    flexDirection: 'column',
    alignItems: 'stretch',
    gap: 8,
  },
  toolHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  hints: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
  },
  offText: {
    color: theme.textMuted,
  },
  mono: {
    color: theme.text,
    fontSize: 12,
    fontFamily: monospaceFontFamily,
  },
});

const styles = StyleSheet.create({
  notice: {
    flexDirection: 'row',
    gap: 10,
    padding: 12,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: theme.border,
    backgroundColor: theme.surface,
  },
  noticeWarning: {
    borderColor: theme.danger,
  },
  noticeBody: {
    flex: 1,
    gap: 8,
  },
  noticeText: {
    ...settingsStyles.muted,
    color: theme.text,
    lineHeight: 18,
  },
  tokenEntry: {
    gap: 10,
  },
});

export const noticeTextStyle = styles.noticeText;
