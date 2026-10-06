import Ionicons from '@expo/vector-icons/Ionicons';
import { Stack, useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
  type LayoutChangeEvent,
  type NativeScrollEvent,
  type NativeSyntheticEvent,
} from 'react-native';

import { AppActionSheet } from '@/components/AppActionSheet';
import { AppGrid, AppTile } from '@/components/AppGrid';
import { LoadState, settingsStyles } from '@/components/SettingsUI';
import { Toast, useToast } from '@/components/Toast';
import { ApiError } from '@/lib/api';
import { buildApp, getApp, listInstalledApps, uninstallInstance, type Instance } from '@/lib/apps';
import { getCurrentSpace, setCurrentSpace } from '@/lib/currentSpace';
import { isReadOnly, spacePath, type Space } from '@/lib/platform';
import { theme } from '@/lib/theme';
import { useLoad } from '@/lib/useAsync';

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
 * The Apps tab (M14-02's Home launcher, renamed in M19-01): a phone-style
 * home screen. Each space is a page of app icons (M19-04), Personal first,
 * swiped between like rooms (M19-05), with its name as the title and dots
 * for the pages; the last page viewed is kept for the session. The system
 * apps (Files, Routines, Settings, Catalog) are a dock under every page;
 * Files opens at the page's space. A dot on an icon marks an update. Tap
 * opens the runner (`[instanceId].tsx`); long press offers update, rebuild,
 * App info (history, publish) and uninstall. Reloads whenever the tab
 * regains focus, so a newly installed app shows up.
 */
export default function HomeScreen() {
  const router = useRouter();
  const { data, error, reload } = useLoad(listInstalledApps);
  const [spaceId, setSpaceId] = useState<string | null>(getCurrentSpace);
  const [pageWidth, setPageWidth] = useState(0);
  const pager = useRef<ScrollView>(null);
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

  const pageIndex = Math.max(0, data?.findIndex((group) => group.space.id === spaceId) ?? 0);

  // Keeps the pager on the current space when it first lays out, resizes,
  // or the page list changes under it.
  const pageCount = data?.length ?? 0;
  useEffect(() => {
    if (pageWidth > 0 && pageCount > 0) pager.current?.scrollTo({ x: pageIndex * pageWidth, animated: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- pageIndex changes from scrolling must not scroll back
  }, [pageWidth, pageCount]);

  if (data === null) {
    return (
      <View style={styles.container}>
        <LoadState error={error} onRetry={reload} />
      </View>
    );
  }

  const current = data[pageIndex]?.space ?? null;
  const updates = data.reduce((n, group) => n + group.instances.filter((i) => i.update).length, 0);

  const showSpace = (index: number) => {
    const space = data[index]?.space;
    if (!space || space.id === current?.id) return;
    setSpaceId(space.id);
    setCurrentSpace(space.id);
  };
  const onPagerLayout = (event: LayoutChangeEvent) => setPageWidth(event.nativeEvent.layout.width);
  const onPagerScroll = (event: NativeSyntheticEvent<NativeScrollEvent>) => {
    if (pageWidth > 0) showSpace(Math.round(event.nativeEvent.contentOffset.x / pageWidth));
  };
  const goToPage = (index: number) => {
    pager.current?.scrollTo({ x: index * pageWidth, animated: true });
    showSpace(index);
  };

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
    <View style={styles.container} testID="home-launcher">
      <Stack.Screen options={{ title: current ? spaceLabel(current) : 'Apps' }} />
      <ScrollView
        ref={pager}
        horizontal
        pagingEnabled
        showsHorizontalScrollIndicator={false}
        onLayout={onPagerLayout}
        onScroll={onPagerScroll}
        scrollEventThrottle={32}
        style={styles.container}
        testID="home-pages"
      >
        {data.map(({ space, instances }) => (
          <ScrollView
            key={space.id}
            style={{ width: pageWidth || undefined }}
            contentContainerStyle={styles.page}
            refreshControl={<RefreshControl refreshing={false} onRefresh={reload} tintColor={theme.textMuted} />}
            testID={`apps-space-${space.slug}`}
          >
            {instances.length === 0 ? (
              <View style={styles.empty} testID="apps-empty">
                <Ionicons name="apps-outline" size={40} color={theme.textMuted} />
                <Text style={styles.emptyTitle}>No apps in {spaceLabel(space)} yet</Text>
                <Text style={settingsStyles.muted}>Install an app from the catalog, or ask the agent to make one.</Text>
              </View>
            ) : (
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
            )}
          </ScrollView>
        ))}
      </ScrollView>

      {data.length > 1 ? (
        <View style={styles.dots} accessibilityRole="tablist" testID="home-space-dots">
          {data.map(({ space }, index) => (
            <Pressable
              key={space.id}
              onPress={() => goToPage(index)}
              hitSlop={8}
              accessibilityRole="tab"
              accessibilityLabel={spaceLabel(space)}
              aria-selected={index === pageIndex}
              testID={`home-space-${space.slug}`}
            >
              <View style={[styles.dot, index === pageIndex && styles.dotSelected]} />
            </Pressable>
          ))}
        </View>
      ) : null}

      <View style={styles.dock} testID="home-system">
        <DockTile slug="files" name="Files" icon="folder" onPress={() => router.push({ pathname: '/files', params: current ? { path: spacePath(current) } : {} })} />
        <DockTile slug="routines" name="Routines" icon="alarm" onPress={() => router.push('/routines')} />
        <DockTile slug="settings" name="Settings" icon="settings" onPress={() => router.push('/settings')} />
        <DockTile
          slug="catalog"
          name="Catalog"
          icon="storefront"
          badge={updates > 0}
          note={updates ? `${updates} update${updates === 1 ? '' : 's'}` : undefined}
          onPress={() => router.push('/apps/catalog')}
          testID="apps-catalog"
        />
      </View>

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

function DockTile({
  slug,
  testID,
  ...tile
}: {
  slug: string;
  name: string;
  icon: string;
  badge?: boolean;
  note?: string;
  onPress: () => void;
  testID?: string;
}) {
  return (
    <View style={styles.dockCell}>
      <AppTile slug={slug} color={SYSTEM_COLOR} testID={testID ?? `home-open-${slug}`} {...tile} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  page: {
    width: '100%',
    maxWidth: 1100,
    alignSelf: 'center',
    padding: 16,
  },
  dots: {
    flexDirection: 'row',
    justifyContent: 'center',
    gap: 10,
    paddingVertical: 10,
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: theme.border,
  },
  dotSelected: {
    backgroundColor: theme.text,
  },
  dock: {
    flexDirection: 'row',
    justifyContent: 'center',
    paddingTop: 12,
    paddingBottom: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: theme.border,
    backgroundColor: theme.surface,
  },
  dockCell: {
    width: 84,
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
