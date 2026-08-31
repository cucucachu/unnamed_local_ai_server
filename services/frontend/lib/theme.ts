import { Platform } from 'react-native';

/** Dark theme constants — exact values from the M2-06 ticket spec. Used
 * throughout the chat screen and tool-item styles instead of hardcoded
 * colors, so a future theming pass has one place to change. */
export const theme = {
  bg: '#0e1116',
  surface: '#161b22',
  accent: '#4f8cff',
  text: '#e6edf3',
  /** Secondary/muted text (timestamps, connection pill, collapsed hints). */
  textMuted: '#8b949e',
  /** Border/divider color, one step up from `surface`. */
  border: '#30363d',
  /** Error-state accent (error items, error pill). */
  danger: '#f85149',
  /** Success-state accent (tool success icon). */
  success: '#3fb950',
} as const;

/** Cross-platform monospace stack for tool-detail blocks (args/result
 * preview). Native gets the platform's real monospace font; web falls back
 * to a standard web-safe monospace font stack. */
export const monospaceFontFamily = Platform.select({
  ios: 'Menlo',
  android: 'monospace',
  default: "'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace",
});
