import type { ReactNode } from 'react';

/** Web opens the chat drawer with its menu button only; a mouse drag selects text. */
export function SwipeToOpen({ children }: { onOpen: () => void; children: ReactNode }) {
  return <>{children}</>;
}
