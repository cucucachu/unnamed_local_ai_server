import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import { Alert, Platform, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';

import { AppActionSheet } from '@/components/AppActionSheet';
import { AppGrid, AppTile } from '@/components/AppGrid';
import { LoadState, SectionTitle, settingsStyles } from '@/components/SettingsUI';
import { Toast, useToast } from '@/components/Toast';
import { ApiError } from '@/lib/api';
import { buildApp, getApp, listInstalledApps, uninstallInstance, type Instance } from '@/lib/apps';
import { isReadOnly, type Space } from '@/lib/platform';
import { theme } from '@/lib/theme';
import { useLoad } from '@/lib/useAsync';

const ALL_SPACES = 'all';

function spaceLabel(space: Space): string {
  return space.kind === 'personal' ? 'Personal' : space.name;
}

/** `Alert.alert` is a no-op on web (`react-native-web`), so web confirms with `window.confirm`. */
function confirmUninstall(name: string, space: string): Promise<boolean> {
  const message = `Uninstall ${name} from ${space}? Its data in this space is deleted.`;
  if (Platform.OS === 'web') return Promise.resolve(window.confirm(message));
  return new Promise((resolve) => {
    Alert.alert('Uninstall app', message, [
      { text: 'Cancel', style: 'cancel', onPress: () => resolve(false) },
      { text: 'Uninstall', style: 'destructive', onPress: () => resolve(true) },
    ]);
  });
}

interface Menu {
  instance: Instance;
  space: Space;
  canRebuild: boolean;
}

/**
 * The Apps tab (M14-02's Home launcher, renamed in M19-01; a home-screen
 * grid since M19-04). System tiles open Files, Routines (M17-09), Settings
 * and the Catalog; installed apps are grouped by space with a switcher, and
 * a dot marks one with an update. Tap opens the runner (`[instanceId].tsx`);
 * long press offers update, rebuild, App info (history, publish) and
 * uninstall. Reloads whenever the tab regains focus, so a newly installed
 * app shows up.
 */
export default function HomeScreen() {
  const router = useRouter();
  const { data, error, reload } = useLoad(listInstalledApps);
  const [spaceId, setSpaceId] = useState(ALL_SPACES);
  const [menu, setMenu] = useState<Menu | null>(null);
  const [busy, setBusy] = useState<'rebuild' | 'uninstall' | null>(null);
  const { message: toast, showToast } = useToast();

  const focused = useRef(false);
  useFocusEffect(
    useCallback(() => {
      if (focused.current) reload();
      focused.current = true;
    }, [reload]),
  );

  const openMenu = useCallback((instance: Instance, space: Space) => {
    setMenu({ instance, space, canRebuild: false });
    getApp(instance.app_id)
      .then((app) =>
        setMenu((current) =>
          current?.instance.id === instance.id ? { ...current, canRebuild: app.source_path !== null } : current,
        ),
      )
      .catch(() => undefined);
  }, []);

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

  const openInstance = (instance: Instance) =>
    router.push({ pathname: '/apps/[instanceId]', params: { instanceId: instance.id } });

  const closeMenu = () => {
    if (busy === null) setMenu(null);
  };

  const handleRebuild = async () => {
    if (menu === null) return;
    const { app } = menu.instance;
    setBusy('rebuild');
    try {
      const result = await buildApp(app.id);
      showToast(result.ok ? `Rebuilt ${app.name}` : `Build failed: ${result.diagnostics[0]?.message ?? 'see App info'}`);
      if (result.ok) setMenu(null);
    } catch (err) {
      showToast(err instanceof ApiError ? err.detail : 'Rebuild failed');
    } finally {
      setBusy(null);
    }
  };

  const handleUninstall = async () => {
    if (menu === null) return;
    const { instance, space } = menu;
    if (!(await confirmUninstall(instance.app.name, spaceLabel(space)))) return;
    setBusy('uninstall');
    try {
      await uninstallInstance(space.id, instance.id);
      setMenu(null);
      showToast(`Uninstalled ${instance.app.name}`);
      reload();
    } catch (err) {
      showToast(err instanceof ApiError ? err.detail : 'Uninstall failed');
    } finally {
      setBusy(null);
    }
  };

  return (
    <View style={styles.container}>
      <ScrollView
        style={styles.container}
        contentContainerStyle={styles.body}
        refreshControl={<RefreshControl refreshing={false} onRefresh={reload} tintColor={theme.textMuted} />}
        testID="home-launcher"
      >
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

        <AppGrid testID="home-system">
          <AppTile
            slug="files"
            name="Files"
            icon="folder"
            color={SYSTEM_COLOR}
            onPress={() => router.push('/files')}
            testID="home-open-files"
          />
          <AppTile
            slug="routines"
            name="Routines"
            icon="alarm"
            color={SYSTEM_COLOR}
            onPress={() => router.push('/routines')}
            testID="home-open-routines"
          />
          <AppTile
            slug="settings"
            name="Settings"
            icon="settings"
            color={SYSTEM_COLOR}
            onPress={() => router.push('/settings')}
            testID="home-open-settings"
          />
          <AppTile
            slug="catalog"
            name="Catalog"
            icon="storefront"
            color={SYSTEM_COLOR}
            badge={updates > 0}
            note={updates ? `${updates} update${updates === 1 ? '' : 's'}` : undefined}
            onPress={() => router.push('/apps/catalog')}
            testID="apps-catalog"
          />
        </AppGrid>

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
              <AppGrid>
                {instances.map((instance) => (
                  <AppTile
                    key={instance.id}
                    slug={instance.app.slug}
                    name={instance.app.name}
                    icon={instance.app.icon}
                    badge={instance.update !== null && !isReadOnly(space)}
                    note={isReadOnly(space) ? 'View only' : undefined}
                    onPress={() => openInstance(instance)}
                    onLongPress={() => openMenu(instance, space)}
                    testID={`apps-open-${instance.app.slug}`}
                  />
                ))}
              </AppGrid>
            </View>
          ))
        )}
      </ScrollView>

      {menu !== null ? (
        <AppActionSheet
          instance={menu.instance}
          spaceName={spaceLabel(menu.space)}
          canRebuild={menu.canRebuild}
          canChange={!isReadOnly(menu.space)}
          busy={busy}
          onClose={closeMenu}
          onOpen={() => {
            setMenu(null);
            openInstance(menu.instance);
          }}
          onUpdate={() => {
            setMenu(null);
            router.push({ pathname: '/apps/update', params: { spaceId: menu.space.id, instanceId: menu.instance.id } });
          }}
          onRebuild={() => void handleRebuild()}
          onInfo={() => {
            setMenu(null);
            router.push({ pathname: '/apps/info/[appId]', params: { appId: menu.instance.app_id } });
          }}
          onUninstall={() => void handleUninstall()}
        />
      ) : null}
      <Toast message={toast} testID="apps-toast" />
    </View>
  );
}

const SYSTEM_COLOR = '#3f4756';

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

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  body: {
    width: '100%',
    maxWidth: 1100,
    alignSelf: 'center',
    padding: 16,
    gap: 20,
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
    gap: 12,
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
