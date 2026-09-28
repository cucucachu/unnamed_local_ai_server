import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect, useRouter } from 'expo-router';
import type { ComponentProps } from 'react';
import { useCallback, useRef } from 'react';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Badge, Card, LoadState, SectionTitle, settingsStyles, ActionButton } from '@/components/SettingsUI';
import { listInstalledApps, type Instance } from '@/lib/apps';
import { isReadOnly } from '@/lib/platform';
import { theme } from '@/lib/theme';
import { useLoad } from '@/lib/useAsync';

type IconName = ComponentProps<typeof Ionicons>['name'];

function appIcon(icon: string | null): IconName {
  return icon && icon in Ionicons.glyphMap ? (icon as IconName) : 'apps-outline';
}

/**
 * Installed app instances, grouped by space. Catalog and update badges are
 * M14-01; the Home launcher (M14-02) still replaces this tab later.
 * Tapping an instance opens the runner (`[instanceId].tsx`).
 * Reloads whenever the tab regains focus, so a newly installed app shows up.
 */
export default function AppsScreen() {
  const router = useRouter();
  const { data, error, reload } = useLoad(listInstalledApps);

  const focused = useRef(false);
  useFocusEffect(
    useCallback(() => {
      if (focused.current) reload();
      focused.current = true;
    }, [reload]),
  );

  if (data === null) {
    return (
      <View style={styles.container}>
        <LoadState error={error} onRetry={reload} />
      </View>
    );
  }

  const groups = data.filter((group) => group.instances.length > 0);
  const updates = groups.reduce((n, group) => n + group.instances.filter((i) => i.update).length, 0);

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.body}
      refreshControl={<RefreshControl refreshing={false} onRefresh={reload} tintColor={theme.textMuted} />}
      testID="apps-list"
    >
      <ActionButton
        label={updates ? `Catalog · ${updates} update${updates === 1 ? '' : 's'}` : 'Catalog'}
        onPress={() => router.push('/apps/catalog')}
        testID="apps-catalog"
      />
      {groups.length === 0 ? (
        <View style={styles.empty} testID="apps-empty">
          <Ionicons name="apps-outline" size={40} color={theme.textMuted} />
          <Text style={styles.emptyTitle}>No apps yet</Text>
          <Text style={settingsStyles.muted}>Install an app from the catalog, or ask the agent to make one.</Text>
        </View>
      ) : (
        groups.map(({ space, instances }) => (
          <View key={space.id} style={styles.group} testID={`apps-space-${space.slug}`}>
            <SectionTitle>{space.kind === 'personal' ? 'Personal' : space.name}</SectionTitle>
            <Card>
              {instances.map((instance, index) => (
                <AppRow
                  key={instance.id}
                  instance={instance}
                  first={index === 0}
                  viewOnly={isReadOnly(space)}
                  onPress={() => router.push({ pathname: '/apps/[instanceId]', params: { instanceId: instance.id } })}
                  onUpdate={
                    instance.update && !isReadOnly(space)
                      ? () =>
                          router.push({
                            pathname: '/apps/update',
                            params: { spaceId: space.id, instanceId: instance.id },
                          })
                      : undefined
                  }
                />
              ))}
            </Card>
          </View>
        ))
      )}
    </ScrollView>
  );
}

function AppRow({
  instance,
  first,
  viewOnly,
  onPress,
  onUpdate,
}: {
  instance: Instance;
  first: boolean;
  viewOnly: boolean;
  onPress: () => void;
  onUpdate?: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={[settingsStyles.row, first && settingsStyles.firstRow]}
      accessibilityRole="button"
      testID={`apps-open-${instance.app.slug}`}
    >
      <Ionicons name={appIcon(instance.app.icon)} size={22} color={theme.accent} />
      <View style={settingsStyles.rowMain}>
        <Text style={settingsStyles.rowTitle}>{instance.app.name}</Text>
        {instance.app.version ? <Text style={settingsStyles.muted}>Version {instance.app.version}</Text> : null}
      </View>
      {instance.update ? (
        <Pressable
          onPress={onUpdate}
          accessibilityRole="button"
          testID={`apps-update-${instance.app.slug}`}
          style={styles.updateHit}
        >
          <Badge label={`Update ${instance.update.version}`} tone="accent" testID={`apps-update-badge-${instance.app.slug}`} />
        </Pressable>
      ) : null}
      {viewOnly ? <Badge label="View only" /> : null}
      <Ionicons name="chevron-forward" size={18} color={theme.textMuted} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  body: {
    width: '100%',
    maxWidth: 640,
    alignSelf: 'center',
    padding: 16,
    gap: 12,
  },
  group: {
    gap: 8,
  },
  empty: {
    alignItems: 'center',
    gap: 8,
    paddingVertical: 48,
  },
  emptyTitle: {
    color: theme.text,
    fontSize: 16,
    fontWeight: '600',
  },
  updateHit: {
    paddingVertical: 4,
    paddingHorizontal: 2,
  },
});
