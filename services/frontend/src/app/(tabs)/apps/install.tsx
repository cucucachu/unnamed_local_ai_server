import { useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { ActionButton, Card, ErrorText, LoadState, SectionTitle, settingsStyles } from '@/components/SettingsUI';
import { describePermissions, installApp, listCatalog, permissionsOf, type CatalogEntry } from '@/lib/apps';
import { isReadOnly, listSpaces, type Space } from '@/lib/platform';
import { theme } from '@/lib/theme';
import { ApiError } from '@/lib/api';
import { useAction, useLoad } from '@/lib/useAsync';

interface InstallTarget {
  space: Space;
  entry: CatalogEntry;
}

async function loadTarget(spaceId: string, appId: string, versionId: string): Promise<InstallTarget> {
  const spaces = await listSpaces();
  const space = spaces.find((s) => s.id === spaceId);
  if (!space) throw new ApiError(404, 'not_found');
  const { entries } = await listCatalog(spaceId);
  const entry = entries.find((e) => e.app.id === appId && e.version.id === versionId) ?? entries.find((e) => e.app.id === appId);
  if (!entry) throw new ApiError(404, 'not_found');
  return { space, entry };
}

/**
 * Install sheet: the published version's name, version, and permissions.
 * Confirming grants those permissions and pins the version in the space.
 */
export default function InstallScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ spaceId: string; appId: string; versionId: string }>();
  const spaceId = Array.isArray(params.spaceId) ? params.spaceId[0] : params.spaceId;
  const appId = Array.isArray(params.appId) ? params.appId[0] : params.appId;
  const versionId = Array.isArray(params.versionId) ? params.versionId[0] : params.versionId;
  const load = useCallback(() => loadTarget(spaceId, appId, versionId), [spaceId, appId, versionId]);
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

  const { space, entry } = data;
  const perms = permissionsOf(entry.version.manifest);
  const lines = describePermissions(perms);
  const canInstall = !isReadOnly(space) && !entry.installed;

  const install = () => {
    setActionError(null);
    void run('install', async () => {
      const instance = await installApp(space.id, entry.app.id, entry.version.id, perms);
      router.replace({ pathname: '/apps/[instanceId]', params: { instanceId: instance.id } });
    });
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.body} testID="apps-install">
      <Card>
        <View style={settingsStyles.cardBody}>
          <Text style={settingsStyles.rowTitle}>{entry.app.name}</Text>
          <Text style={settingsStyles.muted}>
            Version {entry.version.version} · {space.kind === 'personal' ? 'Personal' : space.name}
          </Text>
          {entry.version.manifest.homeai?.description ? (
            <Text style={settingsStyles.muted}>{entry.version.manifest.homeai.description}</Text>
          ) : null}
        </View>
      </Card>

      <SectionTitle>Permissions</SectionTitle>
      <Card>
        <View style={settingsStyles.cardBody} testID="install-permissions">
          {lines.length === 0 ? (
            <Text style={settingsStyles.muted}>This app doesn&apos;t request extra permissions.</Text>
          ) : (
            lines.map((line) => (
              <Text key={line} style={settingsStyles.rowTitle}>
                {line}
              </Text>
            ))
          )}
        </View>
      </Card>

      {actionError ? <ErrorText testID="install-error">{actionError}</ErrorText> : null}

      <ActionButton
        label={entry.installed ? 'Installed' : 'Install'}
        variant="primary"
        onPress={install}
        busy={busyKey === 'install'}
        disabled={!canInstall}
        testID="install-confirm"
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: theme.bg },
  body: { width: '100%', maxWidth: 640, alignSelf: 'center', padding: 16, gap: 12 },
});
