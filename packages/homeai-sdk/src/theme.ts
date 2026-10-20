// The theme apps and the router chrome draw with (M22-03). Expo's top-level
// app.json key `userInterfaceStyle` picks it: "automatic" uses the host's
// scheme, the device's (config `colorScheme`, then `theme` events); "dark" is
// always dark; "light" stays light. The builder puts "automatic" in the bundle
// when app.json has none; a bundle built before M22-03 has none and stays
// light, as it looked then.
import { useSyncExternalStore } from 'react';
import { on } from './bridge';
import { SCHEME_BACKGROUND } from './protocol';

export type ColorScheme = 'light' | 'dark';
export type UserInterfaceStyle = 'light' | 'dark' | 'automatic';

export type Theme = {
  scheme: ColorScheme;
  colors: {
    background: string;
    surface: string;
    text: string;
    textMuted: string;
    border: string;
    tint: string;
    onTint: string;
    danger: string;
    success: string;
    warning: string;
    inactive: string;
  };
  spacing: { xs: number; sm: number; md: number; lg: number; xl: number };
  radius: { sm: number; md: number; lg: number };
  font: { small: number; body: number; title: number; large: number };
};

const base = {
  spacing: { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 },
  radius: { sm: 6, md: 10, lg: 14 },
  font: { small: 13, body: 16, title: 18, large: 28 },
};

export const THEMES: Record<ColorScheme, Theme> = {
  light: {
    scheme: 'light',
    colors: {
      background: SCHEME_BACKGROUND.light,
      surface: '#f4f5f7',
      text: '#111418',
      textMuted: '#6b7280',
      border: '#dddfe3',
      tint: '#208AEF',
      onTint: '#ffffff',
      danger: '#d93036',
      success: '#2f9e44',
      warning: '#c77700',
      inactive: '#8E8E93',
    },
    ...base,
  },
  // Close to the host app's own dark theme (services/frontend/lib/theme.ts).
  dark: {
    scheme: 'dark',
    colors: {
      background: SCHEME_BACKGROUND.dark,
      surface: '#161b22',
      text: '#e6edf3',
      textMuted: '#8b949e',
      border: '#30363d',
      tint: '#4f8cff',
      onTint: '#ffffff',
      danger: '#f85149',
      success: '#3fb950',
      warning: '#d29922',
      inactive: '#6e7681',
    },
    ...base,
  },
};

let style: UserInterfaceStyle = 'light';
let hostScheme: ColorScheme = 'light';
let current: Theme = THEMES.light;
const listeners = new Set<() => void>();

function update() {
  const scheme = style === 'automatic' ? hostScheme : style;
  // The document starts in the host's scheme, which an app's pinned style can override.
  if (typeof document !== 'undefined') {
    document.documentElement.style.backgroundColor = SCHEME_BACKGROUND[scheme];
    document.documentElement.style.colorScheme = scheme;
  }
  if (current.scheme === scheme) return;
  current = THEMES[scheme];
  if (typeof document !== 'undefined') document.body.style.backgroundColor = current.colors.background;
  listeners.forEach((fn) => fn());
}

const isScheme = (v: unknown): v is ColorScheme => v === 'light' || v === 'dark';

/** The app's app.json `userInterfaceStyle` (the builder puts it in the bundle). */
export function setUserInterfaceStyle(next: unknown) {
  style = next === 'automatic' || isScheme(next) ? next : 'light';
  update();
}

/** The host's scheme, from the sandbox config or a `theme` event. */
export function setHostScheme(next: unknown) {
  if (isScheme(next)) hostScheme = next;
  update();
}
on('theme', (data: { colorScheme?: unknown }) => setHostScheme(data?.colorScheme));

export const getTheme = () => current;

export function useTheme(): Theme {
  return useSyncExternalStore(
    (fn) => (listeners.add(fn), () => void listeners.delete(fn)),
    () => current,
    () => current,
  );
}

/** `useColorScheme` from react-native, answering for the app's theme rather than the browser's. */
export function useColorScheme(): ColorScheme {
  return useTheme().scheme;
}
