import { Platform, StyleSheet, useColorScheme } from 'react-native';

export type ColorScheme = 'light' | 'dark';

/** The host's colors (M2-06 for dark). The light one matches the app SDK's
 * light theme (`packages/homeai-sdk/src/theme.ts`), so apps and the host
 * agree. */
export type Palette = {
  scheme: ColorScheme;
  bg: string;
  surface: string;
  accent: string;
  text: string;
  /** Secondary/muted text (timestamps, connection pill, collapsed hints). */
  textMuted: string;
  /** Border/divider color, one step up from `surface`. */
  border: string;
  /** Text and icons drawn on `accent` (primary buttons, selected chips, your chat bubbles). */
  onAccent: string;
  /** Error-state accent (error items, error pill). */
  danger: string;
  /** Success-state accent (tool success icon). */
  success: string;
};

export const palettes: Record<ColorScheme, Palette> = {
  dark: {
    scheme: 'dark',
    bg: '#0e1116',
    surface: '#161b22',
    accent: '#4f8cff',
    text: '#e6edf3',
    textMuted: '#8b949e',
    border: '#30363d',
    onAccent: '#ffffff',
    danger: '#f85149',
    success: '#3fb950',
  },
  light: {
    scheme: 'light',
    bg: '#ffffff',
    surface: '#f4f5f7',
    accent: '#208AEF',
    text: '#111418',
    textMuted: '#6b7280',
    border: '#dddfe3',
    onAccent: '#ffffff',
    danger: '#d93036',
    success: '#2f9e44',
  },
};

/** The device's light/dark setting (the browser's on web); dark when it has none. */
export function useColorSchemeOrDark(): ColorScheme {
  return useColorScheme() === 'light' ? 'light' : 'dark';
}

export function useTheme(): Palette {
  return palettes[useColorSchemeOrDark()];
}

/** A `StyleSheet` per scheme, built on first use: `const useStyles = themedStyles((theme) => ({…}))`. */
export function themedStyles<T extends StyleSheet.NamedStyles<T>>(make: (theme: Palette) => T): () => T {
  const sheets: Partial<Record<ColorScheme, T>> = {};
  return function useStyles() {
    const theme = useTheme();
    return (sheets[theme.scheme] ??= StyleSheet.create(make(theme)));
  };
}

/** Cross-platform monospace stack for tool-detail blocks (args/result
 * preview). Native gets the platform's real monospace font; web falls back
 * to a standard web-safe monospace font stack. */
export const monospaceFontFamily = Platform.select({
  ios: 'Menlo',
  android: 'monospace',
  default: "'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace",
});
