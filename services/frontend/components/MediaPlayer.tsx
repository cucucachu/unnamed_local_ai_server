import Ionicons from '@expo/vector-icons/Ionicons';
import { useAudioPlayer, useAudioPlayerStatus } from 'expo-audio';
import { useVideoPlayer, VideoView } from 'expo-video';
import { useCallback, useState } from 'react';
import { GestureResponderEvent, Pressable, StyleSheet, Text, View } from 'react-native';

import { streamUrl } from '@/lib/media';
import { theme } from '@/lib/theme';

export interface MediaPlayerProps {
  path: string;
  kind: 'video' | 'audio';
}

/**
 * Native (`expo-video` + `expo-audio`) media player, per the M5-02 ticket's
 * §2. Platform-file resolution picks THIS module for iOS/Android and
 * `MediaPlayer.web.tsx` (plain `<video>`/`<audio>` DOM elements) for web —
 * see that file's own docstring for why the web half needs none of this.
 *
 * Dispatches to one of two single-purpose subcomponents below rather than
 * branching inline, so `useVideoPlayer`/`useAudioPlayer` are each only ever
 * called for the kind that's actually being played (both hooks allocate a
 * real native player instance on mount — calling the unused one "just in
 * case" would waste a decoder/audio-session for no reason).
 */
export function MediaPlayer({ path, kind }: MediaPlayerProps) {
  if (kind === 'video') return <NativeVideoPlayer path={path} />;
  return <NativeAudioPlayer path={path} />;
}

/** `nativeControls` (VideoView's default) already renders a full native
 * play/pause/seek transport — no hand-built controls needed for video, per
 * the ticket's own framing ("VideoView + useVideoPlayer for video" as the
 * whole spec, contrasted with audio's explicit "minimal transport" ask).
 * The 16:9 box (rather than measuring the real source dimensions) is the
 * "sized to fit width, letterboxed" ask: `contentFit="contain"` inside a
 * fixed-ratio black box always shows the whole frame without cropping or
 * stretching, with letterbox bars filling any leftover space for sources
 * that aren't actually 16:9. */
function NativeVideoPlayer({ path }: { path: string }) {
  const player = useVideoPlayer(streamUrl(path));

  return (
    <View style={styles.videoBox} testID="media-player-video">
      <VideoView player={player} style={styles.video} contentFit="contain" />
    </View>
  );
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
  const totalSeconds = Math.floor(seconds);
  const minutes = Math.floor(totalSeconds / 60);
  const secs = totalSeconds % 60;
  return `${minutes}:${secs.toString().padStart(2, '0')}`;
}

/** No slider primitive exists anywhere in this app's deps (checked before
 * writing this — no `@react-native-community/slider` or similar), and
 * neither `expo-video`/`expo-audio` ships one — so this is a minimal
 * hand-built scrub bar: a `Pressable` track whose width is measured via
 * `onLayout`, seeking to whatever fraction of that width was tapped. Tap-to-
 * seek only (no drag-to-scrub) — enough to satisfy the ticket's "slider
 * seek bar" ask without a full `PanResponder` gesture implementation for
 * something this app's own player controls don't otherwise need. */
function ScrubBar({ progress, onSeek }: { progress: number; onSeek: (ratio: number) => void }) {
  const [trackWidth, setTrackWidth] = useState(0);

  const handlePress = useCallback(
    (event: GestureResponderEvent) => {
      if (trackWidth <= 0) return;
      const ratio = event.nativeEvent.locationX / trackWidth;
      onSeek(Math.min(1, Math.max(0, ratio)));
    },
    [onSeek, trackWidth],
  );

  return (
    <Pressable
      onLayout={(event) => setTrackWidth(event.nativeEvent.layout.width)}
      onPress={handlePress}
      style={styles.scrubTrack}
      accessibilityRole="adjustable"
      accessibilityLabel="Seek"
      testID="media-player-scrub-bar"
    >
      <View style={[styles.scrubFill, { width: `${Math.min(1, Math.max(0, progress)) * 100}%` }]} />
    </Pressable>
  );
}

function NativeAudioPlayer({ path }: { path: string }) {
  const player = useAudioPlayer(streamUrl(path));
  const status = useAudioPlayerStatus(player);

  const togglePlayback = useCallback(() => {
    if (status.playing) {
      player.pause();
    } else {
      player.play();
    }
  }, [player, status.playing]);

  const handleSeek = useCallback(
    (ratio: number) => {
      if (status.duration > 0) {
        player.seekTo(ratio * status.duration);
      }
    },
    [player, status.duration],
  );

  const progress = status.duration > 0 ? status.currentTime / status.duration : 0;

  return (
    <View style={styles.audioBox} testID="media-player-audio">
      <Pressable
        onPress={togglePlayback}
        style={styles.playButton}
        accessibilityRole="button"
        accessibilityLabel={status.playing ? 'Pause' : 'Play'}
        testID="media-player-play-button"
      >
        <Ionicons name={status.playing ? 'pause' : 'play'} size={32} color={theme.text} />
      </Pressable>
      <ScrubBar progress={progress} onSeek={handleSeek} />
      <Text style={styles.timeText}>
        {formatTime(status.currentTime)} / {formatTime(status.duration)}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  videoBox: {
    width: '100%',
    aspectRatio: 16 / 9,
    backgroundColor: '#000',
    justifyContent: 'center',
  },
  video: {
    width: '100%',
    height: '100%',
  },
  audioBox: {
    width: '100%',
    padding: 24,
    gap: 16,
    alignItems: 'center',
  },
  playButton: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.border,
    alignItems: 'center',
    justifyContent: 'center',
  },
  scrubTrack: {
    width: '100%',
    height: 8,
    borderRadius: 4,
    backgroundColor: theme.surface,
    overflow: 'hidden',
  },
  scrubFill: {
    height: '100%',
    backgroundColor: theme.accent,
  },
  timeText: {
    color: theme.textMuted,
    fontSize: 13,
  },
});
