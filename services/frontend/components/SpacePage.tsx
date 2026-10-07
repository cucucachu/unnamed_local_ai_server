import { Stack, useRouter } from 'expo-router';
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';

import { AppGrid, AppTile } from '@/components/AppGrid';
import { settingsStyles } from '@/components/SettingsUI';
import type { Instance } from '@/lib/apps';
import { isReadOnly, spacePath, type Space } from '@/lib/platform';
import { theme } from '@/lib/theme';

const SYSTEM_COLOR = '#3f4756';

export function spaceLabel(space: Space): string {
  return space.kind === 'personal' ? 'Personal' : space.name;
}

export interface SpacePageProps {
  space: Space;
  instances: Instance[];
  active: boolean;
  width: number;
  onRefresh: () => void;
  onLongPressApp: (instance: Instance, space: Space) => void;
}

/**
 * One space's page of the home pager (M19-04/05; M19-07): its apps as an
 * icon grid. Files comes first and opens at the space; Routines and
 * Settings, which belong to the user rather than a space, are on Personal
 * only; Add app (the space's catalog) comes last where the user can install.
 * A dot marks an update. Tap opens the runner; long press opens the app's
 * action sheet. Sets the header (the space's name) while it's on screen.
 */
export function SpacePage({ space, instances, active, width, onRefresh, onLongPressApp }: SpacePageProps) {
  const router = useRouter();
  const readOnly = isReadOnly(space);
  const label = spaceLabel(space);
  return (
    <ScrollView
      style={{ width: width || undefined }}
      contentContainerStyle={styles.page}
      refreshControl={<RefreshControl refreshing={false} onRefresh={onRefresh} tintColor={theme.textMuted} />}
      testID={`apps-space-${space.slug}`}
    >
      {active ? <Stack.Screen options={{ title: label, headerLeft: () => null }} /> : null}
      <AppGrid>
        <AppTile
          slug="files"
          name="Files"
          icon="folder"
          color={SYSTEM_COLOR}
          onPress={() => router.push({ pathname: '/files', params: { path: spacePath(space) } })}
          testID="home-open-files"
        />
        {space.kind === 'personal' ? (
          <AppTile
            slug="routines"
            name="Routines"
            icon="alarm"
            color={SYSTEM_COLOR}
            onPress={() => router.push('/routines')}
            testID="home-open-routines"
          />
        ) : null}
        {space.kind === 'personal' ? (
          <AppTile
            slug="settings"
            name="Settings"
            icon="settings"
            color={SYSTEM_COLOR}
            onPress={() => router.push('/settings')}
            testID="home-open-settings"
          />
        ) : null}
        {instances.map((instance) => (
          <AppTile
            key={instance.id}
            slug={instance.app.slug}
            name={instance.app.name}
            icon={instance.app.icon}
            badge={instance.update !== null && !readOnly}
            note={readOnly ? 'View only' : undefined}
            onPress={() => router.push({ pathname: '/apps/[instanceId]', params: { instanceId: instance.id } })}
            onLongPress={() => onLongPressApp(instance, space)}
            testID={`apps-open-${instance.app.slug}`}
          />
        ))}
        {!readOnly ? (
          <AppTile
            slug="add"
            name="Add app"
            icon="add"
            color={theme.surface}
            onPress={() => router.push({ pathname: '/apps/catalog', params: { spaceId: space.id } })}
            testID="apps-add"
          />
        ) : null}
      </AppGrid>
      {instances.length === 0 ? (
        <View style={styles.empty} testID="apps-empty">
          <Text style={settingsStyles.muted}>
            {readOnly
              ? `No apps in ${label} yet.`
              : `No apps in ${label} yet. Add one from the catalog, or ask the agent to make one.`}
          </Text>
        </View>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  page: {
    width: '100%',
    maxWidth: 1100,
    alignSelf: 'center',
    padding: 16,
    gap: 24,
  },
  empty: {
    alignItems: 'center',
    paddingHorizontal: 24,
  },
});
