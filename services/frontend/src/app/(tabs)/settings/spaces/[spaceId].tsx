import { useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { useAuth } from '@/components/AuthProvider';
import {
  ActionButton,
  Badge,
  Card,
  ErrorText,
  LoadState,
  SectionTitle,
  Segmented,
  SettingsFrame,
  settingsStyles,
  type SegmentOption,
} from '@/components/SettingsUI';
import { useStepUp } from '@/components/StepUpProvider';
import { Toast, useToast } from '@/components/Toast';
import {
  addMember,
  getSpace,
  listDirectory,
  listMembers,
  platformErrorMessage,
  removeMember,
  updateMemberRole,
  type DirectoryUser,
  type Member,
  type Space,
  type SpaceRole,
} from '@/lib/platform';
import { StepUpCancelledError } from '@/lib/stepUp';
import { theme } from '@/lib/theme';
import { useAction, useLoad } from '@/lib/useAsync';

const ROLE_LABELS: Record<SpaceRole, string> = { owner: 'Owner', editor: 'Editor', viewer: 'Viewer' };

function roleOptions(prefix: string): SegmentOption<SpaceRole>[] {
  return (['viewer', 'editor', 'owner'] as const).map((role) => ({
    value: role,
    label: ROLE_LABELS[role],
    testID: `${prefix}-${role}`,
  }));
}

/** Settings → Spaces → one space: its members, and for an owner of a
 * shared space, adding people from the user directory, changing roles, and
 * removing members. */
export default function SpaceDetailScreen() {
  const { spaceId } = useLocalSearchParams<{ spaceId: string }>();
  const router = useRouter();
  const { state } = useAuth();
  const me = state.phase === 'ready' ? state.user : null;
  const { withStepUp } = useStepUp();
  const { message: toast, showToast } = useToast();
  const { busyKey, run } = useAction(showToast);
  const [adding, setAdding] = useState(false);

  const load = useCallback(
    async (): Promise<{ space: Space; members: Member[] }> => {
      const [space, members] = await Promise.all([getSpace(spaceId), listMembers(spaceId)]);
      return { space, members };
    },
    [spaceId],
  );
  const { data, error, reload } = useLoad(load);
  const space = data?.space ?? null;
  const members = data?.members ?? [];
  const canManage = space?.kind === 'shared' && space.role === 'owner';

  async function handleRole(member: Member, role: SpaceRole) {
    const ok = await run(`role-${member.user_id}`, () =>
      withStepUp(() => updateMemberRole(spaceId, member.user_id, role)),
    );
    if (ok) reload();
  }

  async function handleRemove(member: Member) {
    const ok = await run(`remove-${member.user_id}`, () => withStepUp(() => removeMember(spaceId, member.user_id)));
    if (!ok) return;
    if (member.user_id === me?.id) router.back();
    else reload();
  }

  return (
    <View style={styles.container}>
      <SettingsFrame title={space?.name ?? 'Space'} testID="settings-space-screen">
        {space === null ? (
          <LoadState error={error} onRetry={reload} />
        ) : (
          <>
            <Card>
              <View style={settingsStyles.cardBody}>
                <Text style={settingsStyles.muted}>
                  {space.kind === 'personal'
                    ? 'Your personal space. Only you can see it.'
                    : `Shared space · short name "${space.slug}"`}
                </Text>
                {space.role ? (
                  <Text style={settingsStyles.muted} testID="space-my-role">
                    Your role: <Text style={styles.strong}>{ROLE_LABELS[space.role]}</Text>
                  </Text>
                ) : null}
              </View>
            </Card>

            <SectionTitle>Members</SectionTitle>
            <Card testID="space-members">
              {members.map((member, index) => (
                <View
                  key={member.user_id}
                  style={[settingsStyles.row, index === 0 && settingsStyles.firstRow, styles.memberRow]}
                  testID={`member-row-${member.username}`}
                >
                  <View style={styles.memberHeader}>
                    <View style={settingsStyles.rowMain}>
                      <Text style={settingsStyles.rowTitle}>
                        {member.display_name}
                        {member.user_id === me?.id ? ' (you)' : ''}
                      </Text>
                      <Text style={settingsStyles.muted}>{member.username}</Text>
                    </View>
                    {canManage ? null : <Badge label={ROLE_LABELS[member.role]} testID={`member-role-${member.username}`} />}
                  </View>
                  {canManage ? (
                    <View style={settingsStyles.rowActions}>
                      <Segmented
                        value={member.role}
                        onChange={(role) => handleRole(member, role)}
                        options={roleOptions(`member-role-${member.username}`)}
                        disabled={busyKey !== null}
                      />
                      <ActionButton
                        label="Remove"
                        variant="danger"
                        compact
                        onPress={() => handleRemove(member)}
                        busy={busyKey === `remove-${member.user_id}`}
                        disabled={busyKey !== null}
                        testID={`member-remove-${member.username}`}
                      />
                    </View>
                  ) : null}
                </View>
              ))}
            </Card>
            {canManage ? (
              <ActionButton
                label="Add member"
                variant="primary"
                onPress={() => setAdding(true)}
                testID="space-add-member"
              />
            ) : null}
          </>
        )}
      </SettingsFrame>
      {adding ? (
        <AddMemberModal
          existing={new Set(members.map((m) => m.user_id))}
          addWithStepUp={(userId, role) => withStepUp(() => addMember(spaceId, userId, role))}
          onClose={(added) => {
            setAdding(false);
            if (added) reload();
          }}
        />
      ) : null}
      <Toast message={toast} testID="settings-toast" />
    </View>
  );
}

function AddMemberModal({
  existing,
  addWithStepUp,
  onClose,
}: {
  existing: Set<string>;
  addWithStepUp: (userId: string, role: SpaceRole) => Promise<Member>;
  onClose: (added: boolean) => void;
}) {
  const load = useCallback(() => listDirectory(), []);
  const { data: directory, error: loadError, reload } = useLoad(load);
  const [selected, setSelected] = useState<DirectoryUser | null>(null);
  const [role, setRole] = useState<SpaceRole>('editor');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const candidates = (directory ?? []).filter((user) => !existing.has(user.id));

  async function handleAdd() {
    if (selected === null) return;
    setBusy(true);
    setError(null);
    try {
      await addWithStepUp(selected.id, role);
      onClose(true);
    } catch (caught) {
      if (!(caught instanceof StepUpCancelledError)) setError(platformErrorMessage(caught));
      setBusy(false);
    }
  }

  return (
    <Modal visible transparent animationType="fade" onRequestClose={() => onClose(false)}>
      <View style={styles.overlay}>
        <View style={styles.modalCard} testID="add-member-modal">
          <Text style={styles.modalTitle}>Add member</Text>
          {directory === null ? (
            <LoadState error={loadError} onRetry={reload} />
          ) : candidates.length === 0 ? (
            <Text style={settingsStyles.muted}>Everyone on this server is already a member.</Text>
          ) : (
            <ScrollView style={styles.userList}>
              {candidates.map((user) => {
                const isSelected = selected?.id === user.id;
                return (
                  <Pressable
                    key={user.id}
                    onPress={() => setSelected(user)}
                    style={[styles.userRow, isSelected && styles.userRowSelected]}
                    accessibilityRole="button"
                    accessibilityState={{ selected: isSelected }}
                    testID={`add-member-user-${user.username}`}
                  >
                    <Text style={settingsStyles.rowTitle}>{user.display_name}</Text>
                    <Text style={settingsStyles.muted}>{user.username}</Text>
                  </Pressable>
                );
              })}
            </ScrollView>
          )}
          <Text style={settingsStyles.muted}>Role</Text>
          <Segmented value={role} onChange={setRole} options={roleOptions('add-member-role')} />
          <ErrorText testID="add-member-error">{error}</ErrorText>
          <View style={styles.modalActions}>
            <ActionButton label="Cancel" onPress={() => onClose(false)} testID="add-member-cancel" />
            <ActionButton
              label="Add"
              variant="primary"
              onPress={handleAdd}
              busy={busy}
              disabled={selected === null}
              testID="add-member-submit"
            />
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  strong: {
    color: theme.text,
    fontWeight: '600',
  },
  memberRow: {
    flexDirection: 'column',
    alignItems: 'stretch',
    gap: 10,
  },
  memberHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  modalCard: {
    width: '100%',
    maxWidth: 400,
    maxHeight: '90%',
    backgroundColor: theme.surface,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: theme.border,
    padding: 16,
    gap: 12,
  },
  modalTitle: {
    color: theme.text,
    fontSize: 16,
    fontWeight: '600',
  },
  userList: {
    maxHeight: 280,
  },
  userRow: {
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: 'transparent',
    gap: 2,
  },
  userRowSelected: {
    borderColor: theme.accent,
    backgroundColor: theme.bg,
  },
  modalActions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: 8,
  },
});
