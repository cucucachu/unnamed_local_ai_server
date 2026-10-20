import Ionicons from '@expo/vector-icons/Ionicons';
import { useCallback, useEffect, useRef, useState, type ComponentProps, type ReactNode } from 'react';
import { PanResponder, Pressable, Text, useWindowDimensions, View, type GestureResponderEvent } from 'react-native';

import { themedStyles } from '@/lib/theme';

type IconName = ComponentProps<typeof Ionicons>['name'];

const TILE_COLORS = ['#2f6fdf', '#1f9d74', '#c2410c', '#9333ea', '#d97706', '#0e7490', '#be185d', '#4d7c0f'];

/** A stable colour per app, from its slug. */
export function tileColor(slug: string): string {
  let hash = 0;
  for (let i = 0; i < slug.length; i += 1) hash = (hash * 31 + slug.charCodeAt(i)) | 0;
  return TILE_COLORS[Math.abs(hash) % TILE_COLORS.length];
}

/** `homeai.icon` when it's a real Ionicons glyph, else `null` (the tile shows a letter). */
export function iconGlyph(icon: string | null | undefined): IconName | null {
  return icon && icon in Ionicons.glyphMap ? (icon as IconName) : null;
}

/** Columns by width: 4 on a phone, more on wider screens. */
export function gridColumns(width: number): number {
  if (width >= 1024) return 8;
  if (width >= 700) return 6;
  return 4;
}

export function AppIcon({ icon, slug, name, color }: { icon: string | null; slug: string; name: string; color?: string }) {
  const styles = useStyles();
  const glyph = iconGlyph(icon);
  return (
    <View style={[styles.icon, { backgroundColor: color ?? tileColor(slug) }]}>
      {glyph ? (
        <Ionicons name={glyph} size={30} color="#ffffff" testID={`app-icon-glyph-${slug}`} />
      ) : (
        <Text style={styles.letter} testID={`app-icon-letter-${slug}`}>
          {(name.trim()[0] ?? slug[0] ?? '?').toUpperCase()}
        </Text>
      )}
    </View>
  );
}

export interface AppTileProps {
  slug: string;
  name: string;
  icon: string | null;
  color?: string;
  /** A small accent dot (an update is waiting). */
  badge?: boolean;
  /** Shown under the name, e.g. "View only". */
  note?: string;
  onPress: () => void;
  onLongPress?: () => void;
  testID: string;
}

export function AppTile({ slug, name, icon, color, badge, note, onPress, onLongPress, testID }: AppTileProps) {
  const styles = useStyles();
  return (
    <Pressable
      style={styles.tile}
      onPress={onPress}
      onLongPress={onLongPress}
      delayLongPress={400}
      accessibilityRole="button"
      accessibilityLabel={note ? `${name}, ${note}` : name}
      testID={testID}
    >
      <View>
        <AppIcon icon={icon} slug={slug} name={name} color={color} />
        {badge ? <View style={styles.badge} testID={`${testID}-badge`} /> : null}
      </View>
      <Text style={styles.name} numberOfLines={2}>
        {name}
      </Text>
      {note ? (
        <Text style={styles.note} numberOfLines={1}>
          {note}
        </Text>
      ) : null}
    </Pressable>
  );
}

export interface GridItem {
  key: string;
  node: ReactNode;
  /** Can be dragged while arranging (Add app can't). Fixed items come last. */
  movable?: boolean;
}

const ROW_GAP = 16;

/** The slot under a point of the grid: the first is bottom-right, then leftward, then upward. */
export function slotAt(x: number, y: number, grid: { width: number; height: number }, cellHeight: number, columns: number): number {
  const cellWidth = grid.width / columns;
  const column = Math.min(columns - 1, Math.max(0, columns - 1 - Math.floor(x / cellWidth)));
  const row = Math.max(0, Math.floor((grid.height - y) / (cellHeight + ROW_GAP)));
  return row * columns + column;
}

/**
 * Tiles in rows, 4 across on a phone and more on wider screens, filling
 * from the bottom-right corner leftward and then upward, so the first ones
 * are in thumb reach (#465). While `arranging`, dragging a movable tile
 * moves it; `onArrange` gets the new order as it changes and once more,
 * `done`, on release.
 */
export function AppGrid({
  items,
  arranging = false,
  onArrange,
  testID,
}: {
  items: GridItem[];
  arranging?: boolean;
  onArrange?: (keys: string[], done: boolean) => void;
  testID?: string;
}) {
  const styles = useStyles();
  const { width } = useWindowDimensions();
  const columns = gridColumns(width);
  const grid = useRef<View>(null);
  const [drag, setDrag] = useState<{ key: string; x: number; y: number; width: number } | null>(null);
  // Read and written only by the gesture's callbacks and effects, never while rendering.
  const [shared] = useState(() => ({
    geometry: { pageX: 0, pageY: 0, width: 0, height: 0, cellHeight: 0, grabX: 0, grabY: 0 },
    live: { items, arranging, onArrange, columns },
  }));
  useEffect(() => {
    Object.assign(shared.live, { items, arranging, onArrange, columns });
  });

  const measure = useCallback(
    () =>
      grid.current?.measureInWindow((pageX, pageY, w, h) => Object.assign(shared.geometry, { pageX, pageY, width: w, height: h })),
    [shared],
  );
  useEffect(() => {
    if (arranging) measure();
  }, [arranging, measure]);

  const [responder] = useState(() => {
    const point = (event: GestureResponderEvent) => ({
      x: event.nativeEvent.pageX - shared.geometry.pageX,
      y: event.nativeEvent.pageY - shared.geometry.pageY,
    });
    const movableCount = () => shared.live.items.filter((item) => item.movable !== false).length;
    const slotOf = (x: number, y: number) => {
      const g = shared.geometry;
      return slotAt(x, y, g, g.cellHeight, shared.live.columns);
    };
    const cellOrigin = (slot: number) => {
      const g = shared.geometry;
      const cols = shared.live.columns;
      const cellWidth = g.width / cols;
      return {
        x: g.width - ((slot % cols) + 1) * cellWidth,
        y: g.height - Math.floor(slot / cols) * (g.cellHeight + ROW_GAP) - g.cellHeight,
      };
    };
    let dragged: string | null = null;
    const startable = (event: GestureResponderEvent) => {
      if (!shared.live.arranging) return false;
      const { x, y } = point(event);
      return slotOf(x, y) < movableCount();
    };
    return PanResponder.create({
      onStartShouldSetPanResponderCapture: startable,
      onMoveShouldSetPanResponderCapture: startable,
      onPanResponderTerminationRequest: () => false,
      onPanResponderGrant: (event) => {
        const { x, y } = point(event);
        const slot = slotOf(x, y);
        dragged = shared.live.items[slot]?.key ?? null;
        if (dragged === null) return;
        const origin = cellOrigin(slot);
        Object.assign(shared.geometry, { grabX: x - origin.x, grabY: y - origin.y });
        setDrag({ key: dragged, x: origin.x, y: origin.y, width: shared.geometry.width / shared.live.columns });
      },
      onPanResponderMove: (event) => {
        if (dragged === null) return;
        const { x, y } = point(event);
        const g = shared.geometry;
        setDrag({ key: dragged, x: x - g.grabX, y: y - g.grabY, width: g.width / shared.live.columns });
        const keys = shared.live.items.map((item) => item.key);
        const from = keys.indexOf(dragged);
        const to = Math.min(movableCount() - 1, slotOf(x, y));
        if (from < 0 || to === from) return;
        keys.splice(from, 1);
        keys.splice(to, 0, dragged);
        shared.live.onArrange?.(keys, false);
      },
      onPanResponderRelease: () => finish(),
      onPanResponderTerminate: () => finish(),
    });
    function finish() {
      if (dragged === null) return;
      dragged = null;
      setDrag(null);
      shared.live.onArrange?.(
        shared.live.items.map((item) => item.key),
        true,
      );
    }
  });

  const cellWidth = `${100 / columns}%` as const;
  const floating = drag ? items.find((item) => item.key === drag.key) : undefined;
  return (
    <View testID={testID} onLayout={measure}>
      <View ref={grid} style={styles.grid} {...(arranging ? responder.panHandlers : {})}>
        {items.map((item, index) => (
          <View
            key={item.key}
            style={[{ width: cellWidth }, arranging && item.movable !== false && styles.arrangeCell, drag?.key === item.key && styles.placeholder]}
            onLayout={index === 0 ? (event) => (shared.geometry.cellHeight = event.nativeEvent.layout.height) : undefined}
            pointerEvents={arranging ? 'none' : 'auto'}
            testID={`app-grid-cell-${item.key}`}
          >
            {item.node}
          </View>
        ))}
      </View>
      {floating && drag ? (
        <View pointerEvents="none" style={[styles.floating, { width: drag.width, left: drag.x, top: drag.y }]}>
          {floating.node}
        </View>
      ) : null}
    </View>
  );
}

const useStyles = themedStyles((theme) => ({
  grid: {
    flexDirection: 'row-reverse',
    flexWrap: 'wrap-reverse',
    rowGap: ROW_GAP,
  },
  arrangeCell: {
    opacity: 0.85,
  },
  placeholder: {
    opacity: 0.2,
  },
  floating: {
    position: 'absolute',
    transform: [{ scale: 1.1 }],
  },
  tile: {
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 4,
  },
  icon: {
    width: 60,
    height: 60,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  letter: {
    color: '#ffffff',
    fontSize: 26,
    fontWeight: '700',
  },
  badge: {
    position: 'absolute',
    top: -3,
    right: -3,
    width: 14,
    height: 14,
    borderRadius: 7,
    backgroundColor: theme.danger,
    borderWidth: 2,
    borderColor: theme.bg,
  },
  name: {
    color: theme.text,
    fontSize: 12,
    textAlign: 'center',
  },
  note: {
    color: theme.textMuted,
    fontSize: 10,
    textAlign: 'center',
    marginTop: -4,
  },
}));
