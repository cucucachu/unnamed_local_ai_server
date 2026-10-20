import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';

import { AppGrid, AppTile, type GridItem } from '@/components/AppGrid';
import { PageHeader } from '@/components/PageHeader';
import { useSettingsStyles } from '@/components/SettingsUI';
import { arrangeTiles, type Instance } from '@/lib/apps';
import { isReadOnly, spacePath, type Space } from '@/lib/platform';
import { useTheme } from '@/lib/theme';

const SYSTEM_COLOR = '#3f4756';

export function spaceLabel(space: Space): string {
  return space.kind === 'personal' ? 'Personal' : space.name;
}

export interface SpacePageProps {
  space: Space;
  instances: Instance[];
  /** The user's saved arrangement (`SpaceApps.order`). */
  order: string[];
  width: number;
  /** This page is being arranged: tiles drag, taps don't open them. */
  arranging: boolean;
  onRefresh: () => void;
  onLongPressApp: (instance: Instance, space: Space) => void;
  onArrange: (space: Space) => void;
  onDoneArranging: () => void;
  onSaveOrder: (space: Space, keys: string[]) => void;
}

/**
 * One space's page of the home pager (M19-04/05; M19-07): its apps as an
 * icon grid from the bottom-right (#465), in the user's order for the space
 * (Files first, then Routines and Settings on Personal, then the apps, if
 * never arranged). Add app (the space's catalog) stays last where the user
 * can install. A dot marks an update. Tap opens the runner; long press opens
 * the app's action sheet, or for a built-in tile starts arranging. While
 * arranging, tiles drag to new places and Done ends it. Titled with the
 * space's name.
 */
export function SpacePage({
  space,
  instances,
  order,
  width,
  arranging,
  onRefresh,
  onLongPressApp,
  onArrange,
  onDoneArranging,
  onSaveOrder,
}: SpacePageProps) {
  const settingsStyles = useSettingsStyles();
  const theme = useTheme();
  const router = useRouter();
  const readOnly = isReadOnly(space);
  const label = spaceLabel(space);
  // Follows the drag; the saved order otherwise.
  const [dragOrder, setDragOrder] = useState<string[] | null>(null);

  const builtIn = (slug: string, name: string, icon: string, onPress: () => void, testID: string): GridItem => ({
    key: slug,
    node: (
      <AppTile slug={slug} name={name} icon={icon} color={SYSTEM_COLOR} onPress={onPress} onLongPress={() => onArrange(space)} testID={testID} />
    ),
  });
  const tiles: GridItem[] = [
    builtIn('files', 'Files', 'folder', () => router.push({ pathname: '/files', params: { path: spacePath(space) } }), 'home-open-files'),
    ...(space.kind === 'personal'
      ? [
          builtIn('routines', 'Routines', 'alarm', () => router.push('/routines'), 'home-open-routines'),
          builtIn('settings', 'Settings', 'settings', () => router.push('/settings'), 'home-open-settings'),
        ]
      : []),
    ...instances.map((instance) => ({
      key: instance.id,
      node: (
        <AppTile
          slug={instance.app.slug}
          name={instance.app.name}
          icon={instance.app.icon}
          badge={instance.update !== null && !readOnly}
          note={readOnly ? 'View only' : undefined}
          onPress={() => router.push({ pathname: '/apps/[instanceId]', params: { instanceId: instance.id } })}
          onLongPress={() => onLongPressApp(instance, space)}
          testID={`apps-open-${instance.app.slug}`}
        />
      ),
    })),
  ];
  const byKey = new Map(tiles.map((tile) => [tile.key, tile]));
  const arranged = arrangeTiles(
    tiles.map((tile) => tile.key),
    dragOrder ?? order,
  ).map((key) => byKey.get(key) as GridItem);
  const items: GridItem[] = readOnly
    ? arranged
    : [
        ...arranged,
        {
          key: 'add',
          movable: false,
          node: (
            <AppTile
              slug="add"
              name="Add app"
              icon="add"
              color={theme.surface}
              onPress={() => router.push({ pathname: '/apps/catalog', params: { spaceId: space.id } })}
              testID="apps-add"
            />
          ),
        },
      ];

  const handleArrange = (keys: string[], done: boolean) => {
    const movable = keys.filter((key) => key !== 'add');
    if (!done) {
      setDragOrder(movable);
      return;
    }
    setDragOrder(null);
    onSaveOrder(space, movable);
  };

  return (
    <View style={[styles.container, { width: width || undefined }]} testID={`apps-space-${space.slug}`}>
      <PageHeader
        title={label}
        right={
          arranging ? (
            <Pressable onPress={onDoneArranging} style={styles.done} accessibilityRole="button" testID="apps-arrange-done">
              <Text style={[styles.doneText, { color: theme.accent }]}>Done</Text>
            </Pressable>
          ) : undefined
        }
      />
      <ScrollView
        contentContainerStyle={styles.page}
        scrollEnabled={!arranging}
        refreshControl={<RefreshControl refreshing={false} onRefresh={onRefresh} tintColor={theme.textMuted} />}
      >
        {instances.length === 0 ? (
          <View style={styles.empty} testID="apps-empty">
            <Text style={settingsStyles.muted}>
              {readOnly
                ? `No apps in ${label} yet.`
                : `No apps in ${label} yet. Add one from the catalog, or ask the agent to make one.`}
            </Text>
          </View>
        ) : null}
        {arranging ? (
          <Text style={[settingsStyles.muted, styles.hint]} testID="apps-arrange-hint">
            Drag apps to arrange them. Only you see this order.
          </Text>
        ) : null}
        <AppGrid items={items} arranging={arranging} onArrange={handleArrange} />
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  page: {
    flexGrow: 1,
    justifyContent: 'flex-end',
    width: '100%',
    maxWidth: 1100,
    alignSelf: 'center',
    padding: 16,
    gap: 24,
  },
  hint: {
    textAlign: 'center',
  },
  done: {
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  doneText: {
    fontSize: 16,
    fontWeight: '600',
  },
  empty: {
    alignItems: 'center',
    paddingHorizontal: 24,
  },
});
