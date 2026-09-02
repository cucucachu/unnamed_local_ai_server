import type { CSSProperties } from 'react';

import { streamUrl } from '@/lib/media';
import { theme } from '@/lib/theme';

export interface MediaPlayerProps {
  path: string;
  kind: 'video' | 'audio';
}

/**
 * Web half of the M5-02 platform split — plain `<video controls>` /
 * `<audio controls>` DOM elements, per the ticket ("native browser controls
 * give seek/scrub for free"). react-native-web projects resolving a
 * `.web.tsx` file are allowed to render raw DOM tags directly (this file
 * only ever gets bundled for the web target, via Metro's platform-extension
 * resolution — see `MediaPlayer.tsx`'s docstring) — no `react-native-web`
 * wrapper component needed for something this simple. This project's
 * `tsconfig.json` (`expo/tsconfig.base`'s `"lib": ["DOM", "ESNext"]`)
 * already pulls in the DOM lib, so `<video>`/`<audio>` type-check as
 * ordinary JSX intrinsics with no `as any` cast needed.
 */
export function MediaPlayer({ path, kind }: MediaPlayerProps) {
  const src = streamUrl(path);

  if (kind === 'video') {
    return (
      <div style={videoContainerStyle}>
        <video controls src={src} style={videoStyle} data-testid="media-player-video" />
      </div>
    );
  }

  return (
    <div style={audioContainerStyle}>
      <audio controls src={src} style={audioStyle} data-testid="media-player-audio" />
    </div>
  );
}

const videoContainerStyle: CSSProperties = {
  width: '100%',
  aspectRatio: '16 / 9',
  backgroundColor: '#000',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
};

const videoStyle: CSSProperties = {
  width: '100%',
  height: '100%',
  // `objectFit: 'contain'` is the DOM equivalent of `VideoPlayer.tsx`'s
  // native `contentFit="contain"` — preserves aspect ratio, letterboxed by
  // `videoContainerStyle`'s black background showing through as bars.
  objectFit: 'contain',
  backgroundColor: '#000',
};

const audioContainerStyle: CSSProperties = {
  width: '100%',
  padding: 24,
  display: 'flex',
  justifyContent: 'center',
  backgroundColor: theme.bg,
};

const audioStyle: CSSProperties = {
  width: '100%',
};
