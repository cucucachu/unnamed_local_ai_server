import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect, useRouter } from 'expo-router';
import type { ComponentProps } from 'react';
import { useCallback, useRef, useState } from 'react';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';

import { RoutineInbox, type RoutineInboxHandle } from '@/components/RoutineInbox';
import { Badge, Card, LoadState, SectionTitle, settingsStyles, ActionButton } from '@/components/SettingsUI';
import { listInstalledApps, type Instance } from '@/lib/apps';
import { isReadOnly, type Space } from '@/lib/platform';
import { theme } from '@/lib/theme';
import { useLoad } from '@/lib/useAsync';

type IconName = ComponentProps<typeof Ionicons>['name'];

const ALL_SPACES = 'all';

function appIcon(icon: string | null): IconName {
  return icon && icon in Ionicons.glyphMap ? (icon as IconName) : 'apps-outline';
}

function spaceLabel(space: Space): string {
  return space.kind === 'personal' ? 'Personal' : space.name;
}

/**
 * Home launcher (M14-02): native host screen. M14-03 ships Chat/Files/
 * Settings/Home as image-shipped packages for the agent; this screen still
 * opens the existing native host routes. System tiles open
 * the existing Chat, Files, and Settings host screens; installed apps stay
 * grouped by space with a switcher, catalog entry, and update badges.
 * Tapping an instance opens the runner (`[instanceId].tsx`). Routine runs
 * needing approval, or newly finished, are listed above (M17-05).
 * Reloads whenever the tab regains focus, so a newly installed app shows up.
 */
export default function HomeScreen() {
  const router = useRouter();
  const { data, error, reload } = useLoad(listInstalledApps);
  const [spaceId, setSpaceId] = useState(ALL_SPACES);
  const inbox = useRef<RoutineInboxHandle>(null);

  const focused = useRef(false);
  useFocusEffect(
    useCallback(() => {
      if (focused.current) {
        reload();
        inbox.current?.reload();
      }
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

  const spaces = data.map((group) => group.space);
  const selected = spaceId === ALL_SPACES || spaces.some((space) => space.id === spaceId) ? spaceId : ALL_SPACES;
  const groups =
    selected === ALL_SPACES
      ? data.filter((group) => group.instances.length > 0)
      : data.filter((group) => group.space.id === selected);
  const updates = data.reduce((n, group) => n + group.instances.filter((i) => i.update).length, 0);
  const installedEmpty = groups.every((group) => group.instances.length === 0);

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.body}
      refreshControl={<RefreshControl refreshing={false} onRefresh={reload} tintColor={theme.textMuted} />}
      testID="home-launcher"
    >
      <ActionButton
        label={updates ? `Catalog · ${updates} update${updates === 1 ? '' : 's'}` : 'Catalog'}
        onPress={() => router.push('/apps/catalog')}
        testID="apps-catalog"
      />

      {spaces.length > 1 ? (
        <ScrollView
          horizontal
          nestedScrollEnabled
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.switcher}
          testID="home-space-switcher"
        >
          <SpaceChip label="All" selected={selected === ALL_SPACES} onPress={() => setSpaceId(ALL_SPACES)} testID="home-space-all" />
          {spaces.map((space) => (
            <SpaceChip
              key={space.id}
              label={spaceLabel(space)}
              selected={selected === space.id}
              onPress={() => setSpaceId(space.id)}
              testID={`home-space-${space.slug}`}
            />
          ))}
        </ScrollView>
      ) : null}

      <RoutineInbox ref={inbox} />

      <View style={styles.group} testID="home-system">
        <SectionTitle>System</SectionTitle>
        <Card>
          <SystemRow
            icon="chatbubbles-outline"
            title="Chat"
            first
            onPress={() => router.push('/chat')}
            testID="home-open-chat"
          />
          <SystemRow icon="folder-outline" title="Files" onPress={() => router.push('/files')} testID="home-open-files" />
          <SystemRow
            icon="settings-outline"
            title="Settings"
            onPress={() => router.push('/settings')}
            testID="home-open-settings"
          />
        </Card>
      </View>

      {installedEmpty ? (
        <View style={styles.empty} testID="apps-empty">
          <Ionicons name="apps-outline" size={40} color={theme.textMuted} />
          <Text style={styles.emptyTitle}>No apps yet</Text>
          <Text style={settingsStyles.muted}>Install an app from the catalog, or ask the agent to make one.</Text>
        </View>
      ) : (
        groups.map(({ space, instances }) => (
          <View key={space.id} style={styles.group} testID={`apps-space-${space.slug}`}>
            <SectionTitle>{spaceLabel(space)}</SectionTitle>
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

function SpaceChip({
  label,
  selected,
  onPress,
  testID,
}: {
  label: string;
  selected: boolean;
  onPress: () => void;
  testID: string;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={[styles.chip, selected && styles.chipSelected]}
      accessibilityRole="button"
      accessibilityState={{ selected }}
      testID={testID}
    >
      <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{label}</Text>
    </Pressable>
  );
}

function SystemRow({
  icon,
  title,
  first,
  onPress,
  testID,
}: {
  icon: IconName;
  title: string;
  first?: boolean;
  onPress: () => void;
  testID: string;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={[settingsStyles.row, first && settingsStyles.firstRow]}
      accessibilityRole="button"
      testID={testID}
    >
      <Ionicons name={icon} size={22} color={theme.accent} />
      <View style={settingsStyles.rowMain}>
        <Text style={settingsStyles.rowTitle}>{title}</Text>
      </View>
      <Ionicons name="chevron-forward" size={18} color={theme.textMuted} />
    </Pressable>
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
  switcher: {
    flexDirection: 'row',
    gap: 8,
    paddingVertical: 2,
  },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: theme.border,
    backgroundColor: theme.surface,
  },
  chipSelected: {
    backgroundColor: theme.accent,
    borderColor: theme.accent,
  },
  chipText: {
    color: theme.textMuted,
    fontSize: 13,
    fontWeight: '600',
  },
  chipTextSelected: {
    color: theme.text,
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
