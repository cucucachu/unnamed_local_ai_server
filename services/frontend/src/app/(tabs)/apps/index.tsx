import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect, useRouter } from 'expo-router';
import type { ComponentProps } from 'react';
import { useCallback, useRef } from 'react';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Badge, Card, LoadState, SectionTitle, settingsStyles } from '@/components/SettingsUI';
import { listInstalledApps, type Instance } from '@/lib/apps';
import { theme } from '@/lib/theme';
import { useLoad } from '@/lib/useAsync';

type IconName = ComponentProps<typeof Ionicons>['name'];

function appIcon(icon: string | null): IconName {
  return icon && icon in Ionicons.glyphMap ? (icon as IconName) : 'apps-outline';
}

/**
 * Installed app instances, grouped by space — a placeholder until the M14
 * Home launcher. Tapping one opens the runner (`[instanceId].tsx`).
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

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.body}
      refreshControl={<RefreshControl refreshing={false} onRefresh={reload} tintColor={theme.textMuted} />}
      testID="apps-list"
    >
      {groups.length === 0 ? (
        <View style={styles.empty} testID="apps-empty">
          <Ionicons name="apps-outline" size={40} color={theme.textMuted} />
          <Text style={styles.emptyTitle}>No apps yet</Text>
          <Text style={settingsStyles.muted}>Apps installed in your spaces show up here.</Text>
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
                  viewOnly={space.role !== 'owner' && space.role !== 'editor'}
                  onPress={() => router.push({ pathname: '/apps/[instanceId]', params: { instanceId: instance.id } })}
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
}: {
  instance: Instance;
  first: boolean;
  viewOnly: boolean;
  onPress: () => void;
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
});
