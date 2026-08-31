import { useCallback, useEffect, useRef, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { theme } from '@/lib/theme';

const DEFAULT_TOAST_DURATION_MS = 3000;

/**
 * Shared toast: a `useToast()` hook (message state + auto-dismiss timer)
 * plus a `<Toast>` presentational component.
 *
 * Extracted (M3-05) from the INLINE toast M3-04 built directly inside
 * `src/app/(tabs)/chat/index.tsx` — that screen had its own private
 * `toast`/`showToast`/`toastTimerRef` state trio and rendered a bare `View
 * testID="thread-list-toast"` with no shared component at all. Both that
 * screen (refactored to use this) and the new M3-05 files screen now share
 * this one implementation instead of maintaining two copies of the same
 * "show a message, auto-dismiss after a few seconds" logic.
 */
export function useToast(durationMs: number = DEFAULT_TOAST_DURATION_MS): {
  message: string | null;
  showToast: (text: string) => void;
} {
  const [message, setMessage] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = useCallback(
    (text: string) => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
      setMessage(text);
      timerRef.current = setTimeout(() => setMessage(null), durationMs);
    },
    [durationMs],
  );

  // Same cleanup `chat/index.tsx` had inline: cancel a pending dismiss timer
  // on unmount so it never fires a `setState` after the screen is gone.
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
    };
  }, []);

  return { message, showToast };
}

export interface ToastProps {
  message: string | null;
  /** Kept caller-supplied (not hardcoded) so existing tests/screens can
   * preserve their own established `testID` (e.g. chat's pre-existing
   * `"thread-list-toast"`) across this extraction. */
  testID?: string;
}

export function Toast({ message, testID }: ToastProps) {
  if (!message) return null;
  return (
    <View style={styles.toast} testID={testID}>
      <Text style={styles.toastText}>{message}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  toast: {
    position: 'absolute',
    bottom: 20,
    left: 16,
    right: 16,
    backgroundColor: theme.surface,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: theme.border,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  toastText: {
    color: theme.text,
    fontSize: 14,
    textAlign: 'center',
  },
});
