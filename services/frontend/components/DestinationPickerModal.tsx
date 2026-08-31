import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Modal, Pressable, StyleSheet, Text, View } from 'react-native';

import { FileList } from '@/components/FileList';
import { listFiles, type FileEntry } from '@/lib/files';
import { theme } from '@/lib/theme';

export interface DestinationPickerModalProps {
  /** Directory to open the picker in initially — typically the main files
   * screen's current path, so Move/Copy start browsing from wherever the
   * user already is. */
  initialPath: string;
  onSelect: (destinationDir: string) => void;
  onCancel: () => void;
}

type LoadState = 'loading' | 'error' | 'done';

/**
 * "Select this folder" destination picker for Move/Copy — a minimal
 * folder-browser modal that reuses `FileList` with `dirsOnly`, per the
 * ticket. Has its own internal path state and its own `listFiles` fetch,
 * independent of the main files screen's — it can browse anywhere while
 * the underlying screen's own current directory stays put.
 *
 * No `visible` prop (same reasoning as `PromptModal`): the caller
 * conditionally renders this only while a move/copy is pending, so a fresh
 * mount naturally starts browsing from the current `initialPath` with no
 * extra "reset path when (re-)opened" effect needed.
 *
 * `loadState` is DERIVED from `loadedForPath`/`erroredForPath` rather than
 * an effect calling `setState('loading')` synchronously up front — every
 * actual `setState` call here happens inside the fetch's `.then()`/
 * `.catch()` (i.e. genuinely async, in response to the request settling),
 * which is what this project's `react-hooks/set-state-in-effect` lint rule
 * (`app.json`'s `experiments.reactCompiler`) wants; a naive
 * "`setState('loading')` then fetch" effect body (the shape `useChat.ts`'s
 * pre-existing M3-04 hydration effect already uses, left as-is there since
 * it's out of this ticket's scope) trips that rule.
 */
export function DestinationPickerModal({ initialPath, onSelect, onCancel }: DestinationPickerModalProps) {
  const [path, setPath] = useState(initialPath);
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [loadedForPath, setLoadedForPath] = useState<string | null>(null);
  const [erroredForPath, setErroredForPath] = useState<string | null>(null);
  const [retryAttempt, setRetryAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    listFiles(path)
      .then((listing) => {
        if (cancelled) return;
        setEntries(listing.entries);
        setLoadedForPath(path);
        setErroredForPath(null);
      })
      .catch(() => {
        if (cancelled) return;
        setErroredForPath(path);
      });
    return () => {
      cancelled = true;
    };
    // `retryAttempt` has no bearing on the fetch's own params — it's a
    // pure "force this effect to re-run for the same `path`" bump, the
    // same pattern as `useChat.ts`'s `retryHydration`/`hydrationAttempt`.
  }, [path, retryAttempt]);

  const loadState: LoadState = erroredForPath === path ? 'error' : loadedForPath === path ? 'done' : 'loading';

  const retry = useCallback(() => setRetryAttempt((n) => n + 1), []);

  const segments = path ? path.split('/') : [];

  return (
    <Modal visible animationType="slide" onRequestClose={onCancel} testID="destination-picker-modal">
      <View style={styles.container}>
        <View style={styles.header}>
          <Pressable onPress={onCancel} accessibilityRole="button" testID="destination-picker-cancel">
            <Text style={styles.headerButton}>Cancel</Text>
          </Pressable>
          <Text style={styles.headerTitle} numberOfLines={1}>
            /{segments.join('/')}
          </Text>
          <Pressable onPress={() => onSelect(path)} accessibilityRole="button" testID="destination-picker-select">
            <Text style={[styles.headerButton, styles.selectButton]}>Select this folder</Text>
          </Pressable>
        </View>

        {path ? (
          <Pressable
            style={styles.upRow}
            onPress={() => setPath(segments.slice(0, -1).join('/'))}
            accessibilityRole="button"
            testID="destination-picker-up"
          >
            <Text style={styles.upRowText}>.. (up)</Text>
          </Pressable>
        ) : null}

        {loadState === 'loading' ? (
          <View style={styles.centered}>
            <ActivityIndicator size="large" color={theme.accent} />
          </View>
        ) : loadState === 'error' ? (
          <View style={styles.centered}>
            <Text style={styles.errorText}>Couldn&apos;t load this folder.</Text>
            <Pressable onPress={retry} accessibilityRole="button" testID="destination-picker-retry">
              <Text style={styles.retryText}>Retry</Text>
            </Pressable>
          </View>
        ) : (
          <FileList entries={entries} dirsOnly onPressEntry={(entry) => setPath(entry.path)} />
        )}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: theme.border,
    gap: 8,
  },
  headerButton: {
    color: theme.text,
    fontSize: 14,
  },
  selectButton: {
    color: theme.accent,
    fontWeight: '600',
  },
  headerTitle: {
    flex: 1,
    color: theme.textMuted,
    fontSize: 13,
    textAlign: 'center',
  },
  upRow: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: theme.border,
  },
  upRowText: {
    color: theme.accent,
    fontSize: 14,
  },
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
  },
  errorText: {
    color: theme.danger,
    fontSize: 14,
  },
  retryText: {
    color: theme.accent,
    fontSize: 14,
  },
});
