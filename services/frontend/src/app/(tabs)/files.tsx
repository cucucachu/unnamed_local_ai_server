import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { ActivityIndicator, Alert, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { DestinationPickerModal } from '@/components/DestinationPickerModal';
import { FileActionSheet } from '@/components/FileActionSheet';
import { FileList } from '@/components/FileList';
import { PromptModal } from '@/components/PromptModal';
import { Toast, useToast } from '@/components/Toast';
import { ApiError } from '@/lib/api';
import {
  deletePath,
  downloadFile,
  joinPath,
  listFiles,
  mkdir,
  movePath,
  parentPath,
  pickAndUpload,
  copyPath,
  type FileEntry,
} from '@/lib/files';
import { theme } from '@/lib/theme';

type LoadState = 'loading' | 'error' | 'done';

/** One breadcrumb segment — `path` is the full workspace-relative path this
 * segment navigates to when tapped (`""` for the root "Home" segment). */
interface Crumb {
  label: string;
  path: string;
}

function breadcrumbsFor(path: string): Crumb[] {
  const crumbs: Crumb[] = [{ label: 'Home', path: '' }];
  if (!path) return crumbs;

  let cumulative = '';
  for (const segment of path.split('/')) {
    cumulative = joinPath(cumulative, segment);
    crumbs.push({ label: segment, path: cumulative });
  }
  return crumbs;
}

/** Same `Alert` (native) / `window.confirm` (web) split as `chat/index.tsx`'s
 * own `confirmDeleteThread` — see that function's docstring for why: RN's
 * `Alert.alert` has no real web implementation at all. Not extracted into a
 * shared helper since the ticket only calls out `Toast` for extraction, and
 * the two confirmation MESSAGES are different enough (thread title vs.
 * file/dir name + type) that a shared helper would need its own parameter
 * just to express that difference. */
function confirmDeleteEntry(entry: FileEntry): Promise<boolean> {
  const kind = entry.type === 'dir' ? 'folder' : 'file';
  const message = `Delete ${kind} "${entry.name}"? This can't be undone.`;

  if (Platform.OS === 'web') {
    return Promise.resolve(window.confirm(message));
  }

  return new Promise((resolve) => {
    Alert.alert(`Delete ${kind}`, message, [
      { text: 'Cancel', style: 'cancel', onPress: () => resolve(false) },
      { text: 'Delete', style: 'destructive', onPress: () => resolve(true) },
    ]);
  });
}

function errorDetail(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.detail : fallback;
}

/** Which multi-step flow (if any) is in progress for a given entry — drives
 * the rename/new-folder prompt and the move/copy destination picker. */
type PendingAction =
  | { kind: 'mkdir' }
  | { kind: 'rename'; entry: FileEntry }
  | { kind: 'move'; entry: FileEntry }
  | { kind: 'copy'; entry: FileEntry };

/**
 * M3-05 files screen — single flat route (`src/app/(tabs)/files.tsx`, not a
 * nested stack like `chat/`) with breadcrumb-driven internal path state per
 * the ticket, replacing the M3-05-placeholder that lived here before.
 *
 * Mirrors `chat/index.tsx`'s established list-screen conventions: dark
 * theme via `lib/theme.ts`, `useFocusEffect`-driven fetch (also re-fires on
 * every `path` change — see the inline note below), empty/error states,
 * "mutate then re-fetch the current dir; toast on error" per the ticket's
 * own "optimistic-refresh" wording (distinct from `chat/index.tsx`'s
 * thread-delete flow, which does a true optimistic local removal +
 * restore-on-failure — this ticket's wording only asks for a refresh after
 * a successful mutation, not a client-side guess at the new state).
 */
export default function FilesScreen() {
  const [path, setPath] = useState('');
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [uploading, setUploading] = useState(false);
  const [actionSheetEntry, setActionSheetEntry] = useState<FileEntry | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const { message: toast, showToast } = useToast();

  const load = useCallback((targetPath: string) => {
    setLoadState('loading');
    listFiles(targetPath)
      .then((listing) => {
        setEntries(listing.entries);
        setLoadState('done');
      })
      .catch(() => setLoadState('error'));
  }, []);

  // Same `useFocusEffect` pattern as `ThreadListScreen` (M3-04), but with
  // `path` in the memoized callback's deps: `@react-navigation/native`'s
  // `useFocusEffect` re-subscribes (and immediately re-invokes if already
  // focused) whenever the EFFECT CALLBACK ITSELF changes identity, not just
  // on actual focus/blur transitions — confirmed by reading its source
  // (`useEffect(() => {...; if (navigation.isFocused()) callback(); ...},
  // [navigation, effect])` in `@react-navigation/native`). That means this
  // one hook covers BOTH "refetch when the tab regains focus" AND "refetch
  // when the breadcrumb/a directory tap changes `path`" — no separate plain
  // `useEffect(() => load(path), [path])` is needed alongside it.
  useFocusEffect(
    useCallback(() => {
      load(path);
    }, [path, load]),
  );

  const refresh = useCallback(() => load(path), [load, path]);

  const handlePressEntry = useCallback((entry: FileEntry) => {
    if (entry.type === 'dir') {
      setPath(entry.path);
    } else {
      setActionSheetEntry(entry);
    }
  }, []);

  // Long-press (native) / right-click (web) opens the SAME action sheet for
  // either a file or a directory (per the ticket) — this is the only way to
  // rename/move/copy/delete a directory from the UI, since tapping one just
  // descends into it.
  const handleEntryLongPress = useCallback((entry: FileEntry) => {
    setActionSheetEntry(entry);
  }, []);

  const handleUploadHere = useCallback(async () => {
    if (uploading) return;
    setUploading(true);
    try {
      const result = await pickAndUpload(path);
      if (result === null) return; // user cancelled the picker
      refresh();
    } catch (error) {
      // Logged (not just toasted) because the toast's generic fallback
      // text alone hid the real cause during M3-05 native-device testing
      // (`Unsupported FormDataPart implementation` — see `UploadPart`'s
      // docstring in `lib/files.ts`) for long enough that it needed a
      // live device + terminal log to root-cause.
      console.error('[files] upload failed:', error);
      showToast(errorDetail(error, 'Upload failed'));
    } finally {
      setUploading(false);
    }
  }, [path, refresh, showToast, uploading]);

  const handleMkdirSubmit = useCallback(
    async (name: string) => {
      setPendingAction(null);
      try {
        await mkdir(joinPath(path, name));
        refresh();
      } catch (error) {
        showToast(errorDetail(error, 'Failed to create folder'));
      }
    },
    [path, refresh, showToast],
  );

  const handleRenameSubmit = useCallback(
    async (entry: FileEntry, newName: string) => {
      setPendingAction(null);
      try {
        await movePath(entry.path, joinPath(parentPath(entry.path), newName));
        refresh();
      } catch (error) {
        showToast(errorDetail(error, 'Failed to rename'));
      }
    },
    [refresh, showToast],
  );

  const handleDestinationSelected = useCallback(
    async (entry: FileEntry, mode: 'move' | 'copy', destinationDir: string) => {
      setPendingAction(null);
      const dst = joinPath(destinationDir, entry.name);
      try {
        if (mode === 'move') {
          await movePath(entry.path, dst);
        } else {
          await copyPath(entry.path, dst);
        }
        refresh();
      } catch (error) {
        showToast(errorDetail(error, mode === 'move' ? 'Failed to move' : 'Failed to copy'));
      }
    },
    [refresh, showToast],
  );

  const handleDownload = useCallback(
    (entry: FileEntry) => {
      downloadFile(entry.path).catch((error) => showToast(errorDetail(error, 'Download failed')));
    },
    [showToast],
  );

  const handleDelete = useCallback(
    async (entry: FileEntry) => {
      const confirmed = await confirmDeleteEntry(entry);
      if (!confirmed) return;
      try {
        await deletePath(entry.path);
        refresh();
      } catch (error) {
        showToast(errorDetail(error, 'Failed to delete'));
      }
    },
    [refresh, showToast],
  );

  const crumbs = breadcrumbsFor(path);

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          style={styles.breadcrumb}
          contentContainerStyle={styles.breadcrumbContent}
        >
          {crumbs.map((crumb, index) => (
            <View key={crumb.path} style={styles.breadcrumbItem}>
              <Pressable
                onPress={() => setPath(crumb.path)}
                accessibilityRole="button"
                accessibilityLabel={crumb.label}
                testID="breadcrumb-segment"
              >
                <Text style={[styles.breadcrumbText, index === crumbs.length - 1 && styles.breadcrumbTextActive]}>
                  {crumb.label}
                </Text>
              </Pressable>
              {index < crumbs.length - 1 ? (
                <Ionicons name="chevron-forward" size={14} color={theme.textMuted} style={styles.breadcrumbSep} />
              ) : null}
            </View>
          ))}
        </ScrollView>
        <Pressable onPress={refresh} accessibilityRole="button" accessibilityLabel="Refresh" testID="files-refresh-button">
          <Ionicons name="refresh" size={20} color={theme.text} />
        </Pressable>
      </View>

      {loadState === 'loading' ? (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color={theme.accent} />
        </View>
      ) : loadState === 'error' ? (
        <View style={styles.centered}>
          <Ionicons name="alert-circle-outline" size={28} color={theme.danger} />
          <Text style={styles.errorText}>Couldn&apos;t load this folder.</Text>
          <Pressable style={styles.retryButton} onPress={refresh} accessibilityRole="button" testID="files-retry-button">
            <Text style={styles.retryButtonText}>Retry</Text>
          </Pressable>
        </View>
      ) : entries.length === 0 ? (
        <View style={styles.centered}>
          <Ionicons name="folder-open-outline" size={28} color={theme.textMuted} />
          <Text style={styles.emptyText}>This folder is empty</Text>
        </View>
      ) : (
        <FileList entries={entries} onPressEntry={handlePressEntry} onEntryLongPress={handleEntryLongPress} />
      )}

      <View style={styles.actionBar}>
        <Pressable
          style={styles.actionBarButton}
          onPress={handleUploadHere}
          disabled={uploading}
          accessibilityRole="button"
          testID="files-upload-button"
        >
          {uploading ? (
            <ActivityIndicator size="small" color={theme.text} />
          ) : (
            <Ionicons name="cloud-upload-outline" size={18} color={theme.text} />
          )}
          <Text style={styles.actionBarButtonText}>Upload here</Text>
        </Pressable>
        <Pressable
          style={styles.actionBarButton}
          onPress={() => setPendingAction({ kind: 'mkdir' })}
          accessibilityRole="button"
          testID="files-new-folder-button"
        >
          <Ionicons name="folder-open-outline" size={18} color={theme.text} />
          <Text style={styles.actionBarButtonText}>New folder</Text>
        </Pressable>
      </View>

      <FileActionSheet
        entry={actionSheetEntry}
        onClose={() => setActionSheetEntry(null)}
        onDownload={handleDownload}
        onRename={(entry) => setPendingAction({ kind: 'rename', entry })}
        onMove={(entry) => setPendingAction({ kind: 'move', entry })}
        onCopy={(entry) => setPendingAction({ kind: 'copy', entry })}
        onDelete={handleDelete}
      />

      {pendingAction?.kind === 'mkdir' ? (
        <PromptModal title="New folder" submitLabel="Create" onSubmit={handleMkdirSubmit} onCancel={() => setPendingAction(null)} />
      ) : null}

      {pendingAction?.kind === 'rename' ? (
        <PromptModal
          // Keyed by the entry being renamed so switching from renaming one
          // entry to another (without an intervening close) still remounts
          // fresh with the new entry's own name pre-filled.
          key={pendingAction.entry.path}
          title="Rename"
          initialValue={pendingAction.entry.name}
          submitLabel="Rename"
          onSubmit={(newName) => handleRenameSubmit(pendingAction.entry, newName)}
          onCancel={() => setPendingAction(null)}
        />
      ) : null}

      {pendingAction?.kind === 'move' || pendingAction?.kind === 'copy' ? (
        <DestinationPickerModal
          key={`${pendingAction.kind}-${pendingAction.entry.path}`}
          initialPath={path}
          onSelect={(destinationDir) => handleDestinationSelected(pendingAction.entry, pendingAction.kind, destinationDir)}
          onCancel={() => setPendingAction(null)}
        />
      ) : null}

      {/* Unlike `ThreadListScreen`'s `FlatList` + `RefreshControl`,
          `FileList` doesn't expose pull-to-refresh — the header's explicit
          refresh button covers it instead, which every platform this app
          targets (including web, where pull-to-refresh gestures don't
          exist at all) can use identically. */}

      <Toast message={toast} testID="files-toast" />
    </View>
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
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: theme.border,
    gap: 12,
  },
  breadcrumb: {
    flex: 1,
  },
  breadcrumbContent: {
    alignItems: 'center',
  },
  breadcrumbItem: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  breadcrumbText: {
    color: theme.textMuted,
    fontSize: 14,
  },
  breadcrumbTextActive: {
    color: theme.text,
    fontWeight: '600',
  },
  breadcrumbSep: {
    marginHorizontal: 4,
  },
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    paddingHorizontal: 24,
  },
  errorText: {
    color: theme.danger,
    fontSize: 15,
    textAlign: 'center',
  },
  retryButton: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 8,
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.border,
  },
  retryButtonText: {
    color: theme.text,
    fontSize: 14,
    fontWeight: '600',
  },
  emptyText: {
    color: theme.textMuted,
    fontSize: 16,
  },
  actionBar: {
    flexDirection: 'row',
    borderTopWidth: 1,
    borderTopColor: theme.border,
    paddingHorizontal: 12,
    paddingVertical: 10,
    gap: 10,
  },
  actionBarButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingVertical: 10,
    borderRadius: 10,
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.border,
  },
  actionBarButtonText: {
    color: theme.text,
    fontSize: 14,
    fontWeight: '600',
  },
});
