import { type ReactNode } from 'react';

/**
 * Web half of the M9-04 provider split — keyboard-controller is a native
 * module (and a Reanimated peer). The web export already relies on the
 * viewport resizing (`interactive-widget=resizes-content` in `public/index.html`),
 * so this is a no-op wrapper that keeps `_layout.tsx` platform-agnostic.
 */
export function AppKeyboardProvider({ children }: { children: ReactNode }) {
  return children;
}
