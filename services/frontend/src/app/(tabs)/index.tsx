import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Animated,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  type LayoutChangeEvent,
  type NativeScrollEvent,
  type NativeSyntheticEvent,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppActionSheet } from '@/components/AppActionSheet';
import { drawerWidth } from '@/components/ChatHistoryDrawer';
import { ChatPage, type PagedHistory } from '@/components/ChatPage';
import { SpacePage, spaceLabel } from '@/components/SpacePage';
import { Toast, useToast } from '@/components/Toast';
import { ApiError } from '@/lib/api';
import { buildApp, getApp, listInstalledApps, uninstallInstance, type Instance } from '@/lib/apps';
import { useChatAttention } from '@/lib/chatAttention';
import { pageSwiped, showPage, useCurrentPage, type HomePage } from '@/lib/currentPage';
import { isReadOnly, type Space } from '@/lib/platform';
import { theme } from '@/lib/theme';
import { useKeyboardVisible } from '@/lib/useKeyboardVisible';
import { useLoad } from '@/lib/useAsync';

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
 * The home screen (M19-07): one row of pages you swipe through — Chat, then
 * each space's apps (Personal first, then shared spaces by name). On a
 * phone the chat history is a page left of Chat (M19-08), narrower so the
 * chat still shows beside it, so a swipe right from Chat opens it. The indicator
 * at the bottom (a chat icon with the M17-10 attention count, then a dot
 * per space) shows the page and jumps on tap; it hides while the keyboard
 * is up. The page is kept for the session (`lib/currentPage.ts`); a cold
 * launch opens Chat. `/chat` and `/apps` are links to its pages. Everything
 * else (the runner, Files, Routines, Settings, the catalog) is pushed over
 * it. Reloads the apps whenever it regains focus.
 */
export default function HomeScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const keyboardUp = useKeyboardVisible();
  const attention = useChatAttention();
  const { data, error, reload } = useLoad(listInstalledApps);
  const { page, jump } = useCurrentPage();
  const [width, setWidth] = useState(0);
  const pager = useRef<ScrollView>(null);
  const [menu, setMenu] = useState<Menu | null>(null);
  const [busy, setBusy] = useState<'rebuild' | 'uninstall' | null>(null);
  const { message: toast, showToast } = useToast();

  // A phone has the history page first; its snap points aren't a page
  // width apart, so it snaps to offsets rather than paging.
  const historyPage = Platform.OS !== 'web';
  const lead = historyPage ? drawerWidth(width) : 0;
  const [scrollX] = useState(() => new Animated.Value(Number.MAX_SAFE_INTEGER));
  const paged = useMemo<PagedHistory | undefined>(
    () =>
      historyPage
        ? {
            width: lead,
            scrollX,
            open: () => pager.current?.scrollTo({ x: 0, animated: true }),
            close: () => pager.current?.scrollTo({ x: lead, animated: true }),
          }
        : undefined,
    [historyPage, lead, scrollX],
  );

  const focused = useRef(false);
  useFocusEffect(
    useCallback(() => {
      if (focused.current) reload();
      focused.current = true;
    }, [reload]),
  );

  const groups = useMemo(() => data ?? [], [data]);
  const pages: HomePage[] = useMemo(() => ['chat', ...groups.map((group) => group.space.id)], [groups]);
  const indexOf = useCallback(
    (target: HomePage) => {
      if (target === 'chat') return 0;
      if (target === 'apps') return pages.length > 1 ? 1 : 0;
      return Math.max(0, pages.indexOf(target));
    },
    [pages],
  );
  const activeIndex = indexOf(page);

  // Leaving Chat for a space refetches the spaces and apps: chat may have
  // made or installed one.
  const shownIndex = useRef(activeIndex);
  const noteShown = (index: number) => {
    if (shownIndex.current === 0 && index > 0) reload();
    shownIndex.current = index;
  };

  // A jump (a link, the indicator, the first layout) scrolls to the page,
  // as does a space appearing or going; a swipe is already there. Waits for
  // the spaces before landing on one.
  const ready = page === 'chat' || data !== null;
  useEffect(() => {
    if (width > 0 && ready) pager.current?.scrollTo({ x: lead + indexOf(page) * width, animated: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- jumps, layout and the page count move the pager, not swipes
  }, [jump, width, ready, pages.length]);
  const snapTo = useMemo(() => (historyPage ? [0, ...pages.map((_, index) => lead + index * width)] : undefined), [historyPage, pages, lead, width]);

  const onLayout = (event: LayoutChangeEvent) => setWidth(event.nativeEvent.layout.width);
  const onScroll = (event: NativeSyntheticEvent<NativeScrollEvent>) => {
    const x = event.nativeEvent.contentOffset.x;
    scrollX.setValue(x);
    if (width <= 0 || x < lead / 2) return;
    const index = Math.round((x - lead) / width);
    if (pages[index] === undefined) return;
    noteShown(index);
    pageSwiped(pages[index]);
  };

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
      <ScrollView
        ref={pager}
        horizontal
        pagingEnabled={!historyPage}
        snapToOffsets={snapTo}
        decelerationRate={historyPage ? 'fast' : undefined}
        disableIntervalMomentum
        showsHorizontalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
        onLayout={onLayout}
        onScroll={onScroll}
        scrollEventThrottle={16}
        style={styles.container}
        testID="home-pages"
      >
        <ChatPage width={width} paged={paged} />
        {groups.map(({ space, instances }) => (
          <SpacePage
            key={space.id}
            space={space}
            instances={instances}
            width={width}
            onRefresh={reload}
            onLongPressApp={openMenu}
          />
        ))}
      </ScrollView>

      {!keyboardUp ? (
        <View style={[styles.indicator, { paddingBottom: 10 + insets.bottom }]} accessibilityRole="tablist" testID="home-page-indicator">
          <Pressable
            onPress={() => {
              noteShown(0);
              showPage('chat');
            }}
            hitSlop={8}
            accessibilityRole="tab"
            accessibilityLabel="Chat"
            aria-selected={activeIndex === 0}
            testID="home-page-chat"
          >
            <Ionicons
              name={activeIndex === 0 ? 'chatbubbles' : 'chatbubbles-outline'}
              size={18}
              color={activeIndex === 0 ? theme.text : theme.textMuted}
            />
            {attention > 0 ? (
              <View style={styles.badge} testID="home-page-chat-badge">
                <Text style={styles.badgeText}>{attention > 9 ? '9+' : attention}</Text>
              </View>
            ) : null}
          </Pressable>
          {groups.map(({ space }, index) => (
            <Pressable
              key={space.id}
              onPress={() => {
                noteShown(index + 1);
                showPage(space.id);
              }}
              hitSlop={8}
              accessibilityRole="tab"
              accessibilityLabel={spaceLabel(space)}
              aria-selected={activeIndex === index + 1}
              testID={`home-space-${space.slug}`}
            >
              <View style={[styles.dot, activeIndex === index + 1 && styles.dotSelected]} />
            </Pressable>
          ))}
        </View>
      ) : null}

      {data === null && error !== null ? (
        <Text style={styles.loadError} testID="home-load-error" onPress={reload}>
          Couldn&apos;t load your apps. Tap to retry.
        </Text>
      ) : null}

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
            router.push({ pathname: '/apps/[instanceId]', params: { instanceId: menu.instance.id } });
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

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  indicator: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 14,
    paddingTop: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: theme.border,
    backgroundColor: theme.bg,
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
  badge: {
    position: 'absolute',
    top: -6,
    right: -10,
    minWidth: 16,
    height: 16,
    borderRadius: 8,
    paddingHorizontal: 4,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.danger,
  },
  badgeText: {
    color: '#ffffff',
    fontSize: 10,
    fontWeight: '700',
  },
  loadError: {
    color: theme.danger,
    textAlign: 'center',
    padding: 12,
  },
});
