import { useState } from 'react';
import { ActivityIndicator, Image, StyleSheet, Text, View } from 'react-native';

import { streamUrl } from '@/lib/media';
import { theme } from '@/lib/theme';

export interface ImageViewerProps {
  path: string;
}

/**
 * Issue #124 — in-app image viewer for the Files tab's image entries.
 * Mirrors `MediaPlayer`'s "no download needed" contract, reusing the exact
 * same `GET /api/media/stream?path=...` URL (`lib/media.ts`'s `streamUrl`)
 * as an `<Image>` source instead of a video/audio player — confirmed by
 * reading `services/agent-server/app/api/media.py`'s `_stream` that it
 * streams ANY file under the files root (it only calls
 * `mimetypes.guess_type` to pick a response `Content-Type` header; nothing
 * gates the route on that guess being audio/video), so no new server
 * endpoint is needed just to view an image.
 *
 * No pinch-zoom: no zoom/gesture-based image-viewer library is a dependency
 * of this app today, and the issue's own acceptance criteria only calls for
 * viewing without downloading, not zoom controls — `resizeMode="contain"`
 * fits the whole image in the available space without cropping, which is
 * enough for that. A follow-up issue can add pinch-zoom if it's wanted.
 *
 * One cross-platform implementation (no `.web.tsx` split like
 * `MediaPlayer`): unlike `<video>`/`<audio>`, RN's own `Image` already
 * renders as a plain `<img>` via `react-native-web` — no native-only API
 * (`expo-video`/`expo-audio`) is needed for images on any platform this app
 * targets.
 */
export function ImageViewer({ path }: ImageViewerProps) {
  const [status, setStatus] = useState<'loading' | 'loaded' | 'error'>('loading');

  return (
    <View style={styles.container} testID="image-viewer">
      {status === 'error' ? (
        <Text style={styles.errorText}>Couldn&apos;t load this image.</Text>
      ) : (
        <Image
          source={{ uri: streamUrl(path) }}
          style={styles.image}
          resizeMode="contain"
          onLoad={() => setStatus('loaded')}
          onError={() => setStatus('error')}
          testID="image-viewer-image"
          accessibilityRole="image"
          accessibilityLabel={path}
        />
      )}
      {status === 'loading' ? (
        <ActivityIndicator size="large" color={theme.accent} style={styles.spinner} testID="image-viewer-spinner" />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  image: {
    width: '100%',
    height: '100%',
  },
  spinner: {
    position: 'absolute',
  },
  errorText: {
    color: theme.danger,
    fontSize: 15,
    textAlign: 'center',
    paddingHorizontal: 24,
  },
});
