import Ionicons from '@expo/vector-icons/Ionicons';
import type { ComponentProps, ReactNode } from 'react';
import { Pressable, StyleSheet, Text, useWindowDimensions, View } from 'react-native';

import { theme } from '@/lib/theme';

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

/** Tiles in rows, 4 across on a phone and more on wider screens. */
export function AppGrid({ children, testID }: { children: ReactNode; testID?: string }) {
  const { width } = useWindowDimensions();
  const columns = gridColumns(width);
  const items = Array.isArray(children) ? children.flat().filter(Boolean) : [children];
  return (
    <View style={styles.grid} testID={testID}>
      {items.map((child, index) => (
        <View key={index} style={{ width: `${100 / columns}%` }}>
          {child}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    rowGap: 16,
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
});
