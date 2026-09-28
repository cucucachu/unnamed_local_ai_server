import { useCallback, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import QRCode from 'react-native-qrcode-svg';

import { AuthField } from '@/components/AuthForm';
import {
  ActionButton,
  Badge,
  Card,
  ErrorText,
  LoadState,
  SectionTitle,
  SettingsFrame,
  settingsStyles,
} from '@/components/SettingsUI';
import { useStepUp } from '@/components/StepUpProvider';
import { Toast, useToast } from '@/components/Toast';
import { copyToClipboard } from '@/lib/clipboard';
import {
  adminCreateInvite,
  adminListInvites,
  adminRevokeInvite,
  inviteLink,
  platformErrorMessage,
  type CreatedInvite,
  type Invite,
} from '@/lib/platform';
import { relativeTime } from '@/lib/relativeTime';
import { StepUpCancelledError } from '@/lib/stepUp';
import { monospaceFontFamily, theme } from '@/lib/theme';
import { useAction, useLoad } from '@/lib/useAsync';

/** Settings → Invites (admins): create a single-use link (+ QR) for a new
 * member, and see or revoke past invites. The link is shown only once. */
export default function InvitesScreen() {
  const { withStepUp } = useStepUp();
  const { message: toast, showToast } = useToast();
  const load = useCallback(() => withStepUp(adminListInvites), [withStepUp]);
  const { data: invites, error, reload, setData } = useLoad(load);
  const { busyKey, run } = useAction(showToast);
  const [label, setLabel] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [created, setCreated] = useState<CreatedInvite | null>(null);

  async function handleCreate() {
    setCreating(true);
    setCreateError(null);
    try {
      const invite = await withStepUp(() => adminCreateInvite(label.trim() || undefined));
      setCreated(invite);
      setLabel('');
      setData((previous) => (previous ? [invite, ...previous] : previous));
    } catch (caught) {
      if (!(caught instanceof StepUpCancelledError)) setCreateError(platformErrorMessage(caught));
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(invite: Invite) {
    const ok = await run(invite.id, () => withStepUp(() => adminRevokeInvite(invite.id)));
    if (ok) reload();
  }

  const link = created ? inviteLink(created.token) : null;

  return (
    <View style={styles.container}>
      <SettingsFrame title="Invites" testID="settings-invites-screen">
        <Text style={settingsStyles.muted}>
          An invite link lets one person create a member account. It works once and expires in 7 days.
        </Text>
        <SectionTitle>New invite</SectionTitle>
        <Card>
          <View style={settingsStyles.cardBody}>
            <AuthField
              label="Label (optional)"
              value={label}
              onChangeText={setLabel}
              maxLength={64}
              autoCapitalize="sentences"
              placeholder="For Sam's phone"
              testID="invite-label"
            />
            <ErrorText testID="invite-create-error">{createError}</ErrorText>
            <ActionButton
              label="Create invite link"
              variant="primary"
              onPress={handleCreate}
              busy={creating}
              testID="invite-create"
            />
          </View>
        </Card>

        {created && link ? (
          <Card testID="invite-created">
            <View style={settingsStyles.cardBody}>
              <Text style={settingsStyles.rowTitle}>{created.label ?? 'Invite'} is ready</Text>
              <Text style={settingsStyles.muted}>
                Share this link or let them scan the code. You won&apos;t see this link again.
              </Text>
              <View style={styles.qr} testID="invite-qr">
                <QRCode value={link} size={200} quietZone={10} />
              </View>
              <Text style={styles.link} selectable testID="invite-link">
                {link}
              </Text>
              <View style={settingsStyles.rowActions}>
                <ActionButton
                  label="Copy link"
                  onPress={() => {
                    void copyToClipboard(link).then(
                      () => showToast('Link copied'),
                      () => showToast("Couldn't copy. Select the link and copy it by hand."),
                    );
                  }}
                  testID="invite-copy"
                />
                <ActionButton label="Done" onPress={() => setCreated(null)} testID="invite-done" />
              </View>
            </View>
          </Card>
        ) : null}

        <SectionTitle>Invites</SectionTitle>
        {invites === null ? (
          <LoadState error={error} onRetry={reload} />
        ) : invites.length === 0 ? (
          <Text style={settingsStyles.muted}>No invites yet.</Text>
        ) : (
          <Card>
            {invites.map((invite, index) => (
              <View
                key={invite.id}
                style={[settingsStyles.row, index === 0 && settingsStyles.firstRow]}
                testID={`invite-row-${invite.id}`}
              >
                <View style={settingsStyles.rowMain}>
                  <Text style={settingsStyles.rowTitle}>{invite.label ?? 'Untitled invite'}</Text>
                  <Text style={settingsStyles.muted}>
                    Created {relativeTime(invite.created_at)}
                    {invite.status === 'pending' ? ` · expires ${expiresIn(invite.expires_at)}` : ''}
                  </Text>
                </View>
                <Badge
                  label={invite.status}
                  tone={invite.status === 'pending' ? 'accent' : invite.status === 'revoked' ? 'danger' : 'muted'}
                  testID={`invite-status-${invite.id}`}
                />
                {invite.status === 'pending' ? (
                  <ActionButton
                    label="Revoke"
                    variant="danger"
                    compact
                    onPress={() => handleRevoke(invite)}
                    busy={busyKey === invite.id}
                    disabled={busyKey !== null && busyKey !== invite.id}
                    testID={`invite-revoke-${invite.id}`}
                  />
                ) : null}
              </View>
            ))}
          </Card>
        )}
      </SettingsFrame>
      <Toast message={toast} testID="settings-toast" />
    </View>
  );
}

function expiresIn(iso: string): string {
  const days = Math.ceil((new Date(iso).getTime() - Date.now()) / (24 * 60 * 60 * 1000));
  if (!Number.isFinite(days) || days <= 0) return 'soon';
  return days === 1 ? 'in 1 day' : `in ${days} days`;
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  qr: {
    alignSelf: 'center',
    padding: 4,
    backgroundColor: '#ffffff',
    borderRadius: 8,
  },
  link: {
    color: theme.text,
    fontFamily: monospaceFontFamily,
    fontSize: 13,
  },
});
