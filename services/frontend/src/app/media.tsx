import Ionicons from '@expo/vector-icons/Ionicons';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { MediaPlayer } from '@/components/MediaPlayer';
import { mediaKind } from '@/lib/media';
import { theme } from '@/lib/theme';

/**
 * M5-02 §3 — modal player screen, registered as a `presentation: 'modal'`
 * `Stack.Screen` in `_layout.tsx` (a sibling of the `(tabs)` group route,
 * not nested inside it — the ticket's own files-screen hookup navigates
 * here directly from the Files tab, so this needs to be reachable from
 * outside that tab's own nested stack).
 *
 * `path`/`kind` arrive as route params (see `files.tsx`'s `handlePressEntry`
 * / `FileActionSheet`'s "Play" action, both of which `router.push` here).
 * `kind` is re-derived from `path` via `mediaKind` rather than trusted as-is
 * — defensive against a stale/hand-typed `kind` param ever disagreeing with
 * the actual file extension (e.g. a deep link) — and the screen renders a
 * plain "can't play this file" message instead of crashing if `path` turns
 * out not to be a recognized media file at all.
 */
export default function MediaScreen() {
  const { path } = useLocalSearchParams<{ path: string; kind: string }>();
  const router = useRouter();

  const decodedPath = path ?? '';
  const kind = mediaKind(decodedPath);
  const filename = decodedPath.split('/').pop() || decodedPath;

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Pressable
          onPress={() => router.back()}
          style={styles.closeButton}
          accessibilityRole="button"
          accessibilityLabel="Close"
          testID="media-close-button"
        >
          <Ionicons name="close" size={26} color={theme.text} />
        </Pressable>
        <Text style={styles.title} numberOfLines={1}>
          {filename}
        </Text>
      </View>

      <View style={styles.playerWrap}>
        {kind !== null ? (
          <MediaPlayer path={decodedPath} kind={kind} />
        ) : (
          <Text style={styles.errorText}>Can&apos;t play this file.</Text>
        )}
      </View>
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
    gap: 12,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: theme.border,
  },
  closeButton: {
    padding: 4,
  },
  title: {
    flex: 1,
    color: theme.text,
    fontSize: 16,
    fontWeight: '600',
  },
  playerWrap: {
    flex: 1,
    justifyContent: 'center',
  },
  errorText: {
    color: theme.danger,
    fontSize: 15,
    textAlign: 'center',
    paddingHorizontal: 24,
  },
});
