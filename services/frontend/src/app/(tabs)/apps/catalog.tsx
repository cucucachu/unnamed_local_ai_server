import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback } from 'react';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import Ionicons from '@expo/vector-icons/Ionicons';

import { Badge, Card, LoadState, SectionTitle, settingsStyles } from '@/components/SettingsUI';
import { listCatalog, type CatalogEntry } from '@/lib/apps';
import { isReadOnly, listSpaces, type Space } from '@/lib/platform';
import { theme } from '@/lib/theme';
import { useLoad } from '@/lib/useAsync';

interface SpaceCatalog {
  space: Space;
  entries: CatalogEntry[];
}

async function loadCatalogs(onlySpaceId?: string): Promise<SpaceCatalog[]> {
  const spaces = (await listSpaces())
    .filter((space) => space.archived_at === null && (!onlySpaceId || space.id === onlySpaceId))
    .sort((a, b) => (a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === 'personal' ? -1 : 1));
  return Promise.all(spaces.map(async (space) => ({ space, entries: (await listCatalog(space.id)).entries })));
}

/**
 * Published apps listed in the catalogs of the user's spaces (M14-01).
 * Tapping an entry that isn't installed opens the install sheet. A space
 * page's Add app tile opens just that space's catalog (`?spaceId=`, M19-07).
 */
export default function CatalogScreen() {
  const router = useRouter();
  const { spaceId } = useLocalSearchParams<{ spaceId?: string }>();
  const load = useCallback(() => loadCatalogs(spaceId), [spaceId]);
  const { data, error, reload } = useLoad(load);

  const open = useCallback(
    (space: Space, entry: CatalogEntry) => {
      if (entry.installed) {
        if (entry.instance_id) {
          router.push({ pathname: '/apps/[instanceId]', params: { instanceId: entry.instance_id } });
        }
        return;
      }
      router.push({
        pathname: '/apps/install',
        params: { spaceId: space.id, appId: entry.app.id, versionId: entry.version.id },
      });
    },
    [router],
  );

  if (data === null) {
    return (
      <View style={styles.container}>
        <LoadState error={error} onRetry={reload} />
      </View>
    );
  }

  const groups = data.filter((group) => group.entries.length > 0);
  const only = spaceId ? data[0]?.space : undefined;
  const onlyLabel = only ? (only.kind === 'personal' ? 'Personal' : only.name) : null;

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.body}
      refreshControl={<RefreshControl refreshing={false} onRefresh={reload} tintColor={theme.textMuted} />}
      testID="apps-catalog"
    >
      {onlyLabel ? <Stack.Screen options={{ title: `Add to ${onlyLabel}` }} /> : null}
      {groups.length === 0 ? (
        <View style={styles.empty} testID="catalog-empty">
          <Text style={styles.emptyTitle}>{onlyLabel ? `Nothing to add to ${onlyLabel} yet` : 'Nothing in the catalog yet'}</Text>
          <Text style={settingsStyles.muted}>
            Publish an app from its info screen to list it in a space other people can install from.
          </Text>
        </View>
      ) : (
        groups.map(({ space, entries }) => (
          <View key={space.id} style={styles.group} testID={`catalog-space-${space.slug}`}>
            <SectionTitle>{space.kind === 'personal' ? 'Personal' : space.name}</SectionTitle>
            <Card>
              {entries.map((entry, index) => (
                <Pressable
                  key={entry.app.id}
                  onPress={() => open(space, entry)}
                  disabled={isReadOnly(space) && !entry.installed}
                  style={[settingsStyles.row, index === 0 && settingsStyles.firstRow]}
                  accessibilityRole="button"
                  testID={`catalog-app-${entry.app.slug}`}
                >
                  <Ionicons name="cube-outline" size={22} color={theme.accent} />
                  <View style={settingsStyles.rowMain}>
                    <Text style={settingsStyles.rowTitle}>{entry.app.name}</Text>
                    <Text style={settingsStyles.muted}>Version {entry.version.version}</Text>
                  </View>
                  {entry.installed ? <Badge label="Installed" /> : isReadOnly(space) ? <Badge label="View only" /> : null}
                  <Ionicons name="chevron-forward" size={18} color={theme.textMuted} />
                </Pressable>
              ))}
            </Card>
          </View>
        ))
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: theme.bg },
  body: { width: '100%', maxWidth: 640, alignSelf: 'center', padding: 16, gap: 12 },
  group: { gap: 8 },
  empty: { gap: 8, paddingVertical: 48 },
  emptyTitle: { color: theme.text, fontSize: 16, fontWeight: '600' },
});
