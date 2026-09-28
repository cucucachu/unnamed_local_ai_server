import { useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { ActionButton, Card, ErrorText, LoadState, SectionTitle, settingsStyles } from '@/components/SettingsUI';
import { isReadOnly } from '@/lib/platform';
import { describePermissions, findInstance, updateInstance } from '@/lib/apps';
import { theme } from '@/lib/theme';
import { useAction, useLoad } from '@/lib/useAsync';

/**
 * Approve pinning a published install to a newer version. Permission diffs
 * are listed and sent as `granted_permissions` with the update.
 */
export default function UpdateScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ spaceId: string; instanceId: string }>();
  const instanceId = Array.isArray(params.instanceId) ? params.instanceId[0] : params.instanceId;
  const load = useCallback(() => findInstance(instanceId), [instanceId]);
  const { data, error, reload } = useLoad(load);
  const [actionError, setActionError] = useState<string | null>(null);
  const { busyKey, run } = useAction(setActionError);

  if (data === null) {
    return (
      <View style={styles.container}>
        <LoadState error={error} onRetry={reload} />
      </View>
    );
  }

  const { space, instance } = data;
  const update = instance.update;
  const perms = update?.permissions ?? {};
  const lines = describePermissions(perms);
  const canUpdate = Boolean(update) && !isReadOnly(space);

  const apply = () => {
    setActionError(null);
    void run('update', async () => {
      if (!update) return;
      await updateInstance(space.id, instance.id, update.id, perms);
      router.replace({ pathname: '/apps/[instanceId]', params: { instanceId: instance.id } });
    });
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.body} testID="apps-update">
      <Card>
        <View style={settingsStyles.cardBody}>
          <Text style={settingsStyles.rowTitle}>{instance.app.name}</Text>
          <Text style={settingsStyles.muted}>
            {instance.app.version ? `Installed ${instance.app.version}` : 'Installed'}
            {update ? ` → ${update.version}` : ''}
          </Text>
        </View>
      </Card>

      <SectionTitle>Permissions</SectionTitle>
      <Card>
        <View style={settingsStyles.cardBody} testID="update-permissions">
          {lines.length === 0 ? (
            <Text style={settingsStyles.muted}>This version doesn&apos;t request extra permissions.</Text>
          ) : (
            lines.map((line) => (
              <Text key={line} style={settingsStyles.rowTitle}>
                {line}
              </Text>
            ))
          )}
        </View>
      </Card>

      {actionError ? <ErrorText testID="update-error">{actionError}</ErrorText> : null}

      <ActionButton
        label={update ? `Update to ${update.version}` : 'Up to date'}
        variant="primary"
        onPress={apply}
        busy={busyKey === 'update'}
        disabled={!canUpdate}
        testID="update-confirm"
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: theme.bg },
  body: { width: '100%', maxWidth: 640, alignSelf: 'center', padding: 16, gap: 12 },
});
