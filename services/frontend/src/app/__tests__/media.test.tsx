import { createElement } from 'react';
import { Text as RNText } from 'react-native';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

// Same mocking shape as `settings.test.tsx` — this suite is about the
// SCREEN's own routing logic (which viewer/player it picks for a given
// `path`), not `useLocalSearchParams`/`useRouter` themselves.
const mockBack = jest.fn();
let mockSearchParams: { path?: string; kind?: string } = {};
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack }),
  useLocalSearchParams: () => mockSearchParams,
}));

// `../media` statically imports `MediaPlayer.tsx`, which statically imports
// real `expo-video`/`expo-audio` — merely IMPORTING those (module-level
// code, before any hook is ever called) throws in this jest environment
// (confirmed: no mock for either exists anywhere in this repo today, and
// `expo-audio`'s own module init reads a native-module field that's
// `undefined` under `jest-expo`'s test environment). Mocked here purely so
// this suite can import `MediaScreen` at all — every test below only ever
// exercises the `path` -> `image` / `null` branches, never `video`/`audio`
// (see the note further down for why that half is covered at the
// `files.tsx` routing level instead).
jest.mock('expo-video', () => ({
  useVideoPlayer: () => ({}),
  VideoView: () => null,
}));
jest.mock('expo-audio', () => ({
  useAudioPlayer: () => ({}),
  useAudioPlayerStatus: () => ({ playing: false, currentTime: 0, duration: 0 }),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import MediaScreen from '../media';

function render(element: React.ReactElement): ReactTestRenderer {
  let renderer!: ReactTestRenderer;
  act(() => {
    renderer = create(element);
  });
  return renderer;
}

function textOf(renderer: ReactTestRenderer): string {
  return renderer.root
    .findAllByType(RNText)
    .map((node) => String(node.props.children ?? ''))
    .join(' | ');
}

beforeEach(() => {
  mockBack.mockReset();
  mockSearchParams = {};
});

describe('MediaScreen — issue #124 image routing', () => {
  it('renders the ImageViewer for an image path, not a fallback message', () => {
    mockSearchParams = { path: 'photo.png' };

    const renderer = render(createElement(MediaScreen));

    expect(renderer.root.findAllByProps({ testID: 'image-viewer' }).length).toBeGreaterThan(0);
    expect(textOf(renderer)).not.toContain("Can't preview this file.");
  });

  // Video/audio paths route to `MediaPlayer`, which reaches for real native
  // `expo-video`/`expo-audio` hooks with no jest mock in this repo today
  // (confirmed: neither `jest.setup.js` nor `jest-expo`'s own presets mock
  // either module) — so this suite deliberately never mounts `MediaScreen`
  // with a video/audio `path`. `src/app/(tabs)/__tests__/files.test.tsx`
  // already covers that routing decision at the `files.tsx` level (it
  // asserts `router.push`'s `kind` param, without ever mounting the real
  // player), which is the same style of coverage used here for "does this
  // path resolve to a real component vs a string" without invoking a
  // native module.

  it('shows a fallback message for a path that is neither media nor image', () => {
    mockSearchParams = { path: 'document.pdf' };

    const renderer = render(createElement(MediaScreen));

    expect(textOf(renderer)).toContain("Can't preview this file.");
  });

  it('re-derives kind from the path rather than trusting a stale kind param', () => {
    // Deliberately mismatched `kind` param — the screen must still route by
    // the real extension in `path`, per its own docstring.
    mockSearchParams = { path: 'photo.png', kind: 'video' };

    const renderer = render(createElement(MediaScreen));

    expect(renderer.root.findAllByProps({ testID: 'image-viewer' }).length).toBeGreaterThan(0);
  });
});
