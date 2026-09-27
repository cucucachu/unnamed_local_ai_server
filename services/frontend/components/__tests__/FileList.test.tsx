import { createElement } from 'react';
import { Platform, Text as RNText } from 'react-native';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import type { FileEntry } from '@/lib/files';

import { FileList } from '../FileList';

const DIR_A: FileEntry = { name: 'alpha', path: 'alpha', type: 'dir', size: 0, mtime: '2026-08-30T10:00:00.000Z', mime: null };
const DIR_B: FileEntry = { name: 'beta', path: 'beta', type: 'dir', size: 0, mtime: '2026-08-30T10:00:00.000Z', mime: null };
const FILE_A: FileEntry = {
  name: 'notes.txt',
  path: 'notes.txt',
  type: 'file',
  size: 1536,
  mtime: '2026-08-30T19:55:00.000Z',
  mime: 'text/plain',
};
const IMAGE_FILE: FileEntry = {
  name: 'photo.png',
  path: 'photo.png',
  type: 'file',
  size: 2048,
  mtime: '2026-08-30T19:55:00.000Z',
  mime: 'image/png',
};
const VIDEO_FILE: FileEntry = {
  name: 'clip.mp4',
  path: 'clip.mp4',
  type: 'file',
  size: 4096,
  mtime: '2026-08-30T19:55:00.000Z',
  mime: 'video/mp4',
};

// Same "walk <Text> nodes" helper as `ThreadListScreen`'s own test suite
// (`chat/__tests__/index.test.tsx`) — a `FlatList`'s rendered output can't
// be `JSON.stringify`'d directly (circular fiber references).
function textOf(renderer: ReactTestRenderer): string {
  return renderer.root
    .findAllByType(RNText)
    .map((node) => {
      const children = node.props.children;
      return Array.isArray(children) ? children.join('') : String(children ?? '');
    })
    .join(' | ');
}

function render(element: React.ReactElement): ReactTestRenderer {
  let renderer!: ReactTestRenderer;
  act(() => {
    renderer = create(element);
  });
  return renderer;
}

/** `Pressable` forwards a subset of its own props (`testID`,
 * `accessibilityLabel`, style, ...) down to its underlying host node, so a
 * lookup by either alone (via `findAllByProps`/`findByProps`) can match
 * BOTH the composite `Pressable` element and its rendered host child.
 * `onPress` itself is handled internally by `Pressable` and never
 * forwarded, so it uniquely identifies the actual `Pressable` instance
 * among all of a row's matches. */
function findRow(renderer: ReactTestRenderer, name: string) {
  const candidates = renderer.root.findAllByProps({ accessibilityLabel: name });
  const row = candidates.find((node) => typeof node.props.onPress === 'function');
  if (!row) throw new Error(`no Pressable row found for "${name}"`);
  return row;
}

describe('FileList', () => {
  it('renders entries in the order given (dirs-first per the server, never re-sorted client-side)', () => {
    const renderer = render(createElement(FileList, { entries: [DIR_A, DIR_B, FILE_A], onPressEntry: jest.fn() }));

    const rendered = textOf(renderer);
    expect(rendered.indexOf('alpha')).toBeLessThan(rendered.indexOf('beta'));
    expect(rendered.indexOf('beta')).toBeLessThan(rendered.indexOf('notes.txt'));
  });

  it('shows a size + relative-mtime subtitle for files, but not for dirs', () => {
    const renderer = render(createElement(FileList, { entries: [DIR_A, FILE_A], onPressEntry: jest.fn() }));

    const rendered = textOf(renderer);
    expect(rendered).toContain('1.5 KB');
  });

    it('marks the highlighted entry with file-entry-highlighted (M9-03)', () => {
      const renderer = render(
        createElement(FileList, {
          entries: [DIR_A, FILE_A],
          onPressEntry: jest.fn(),
          highlightedPath: FILE_A.path,
        }),
      );

      const highlighted = renderer.root
        .findAllByProps({ testID: 'file-entry-highlighted' })
        .find((node) => typeof node.props.onPress === 'function');
      expect(highlighted?.props.accessibilityLabel).toBe(FILE_A.name);
      expect(
        renderer.root.findAllByProps({ testID: 'file-row' }).some((node) => typeof node.props.onPress === 'function'),
      ).toBe(true);
    });

    it('calls onPressEntry with the tapped entry', () => {
    const onPressEntry = jest.fn();
    const renderer = render(createElement(FileList, { entries: [DIR_A, FILE_A], onPressEntry }));

    const row = findRow(renderer, FILE_A.name);
    act(() => {
      row.props.onPress();
    });

    expect(onPressEntry).toHaveBeenCalledWith(FILE_A);
  });

  describe('dirsOnly', () => {
    it('filters out files, keeping only directories', () => {
      const renderer = render(createElement(FileList, { entries: [DIR_A, FILE_A, DIR_B], onPressEntry: jest.fn(), dirsOnly: true }));

      const rendered = textOf(renderer);
      expect(rendered).toContain('alpha');
      expect(rendered).toContain('beta');
      expect(rendered).not.toContain('notes.txt');
    });
  });

  describe('onEntryLongPress', () => {
    it('fires on native onLongPress', () => {
      const onEntryLongPress = jest.fn();
      const renderer = render(createElement(FileList, { entries: [FILE_A], onPressEntry: jest.fn(), onEntryLongPress }));

      const row = findRow(renderer, FILE_A.name);
      act(() => {
        row.props.onLongPress();
      });

      expect(onEntryLongPress).toHaveBeenCalledWith(FILE_A);
    });

    it('fires on web onContextMenu (right-click), and prevents the default browser menu', () => {
      Platform.OS = 'web';
      const onEntryLongPress = jest.fn();
      const renderer = render(createElement(FileList, { entries: [FILE_A], onPressEntry: jest.fn(), onEntryLongPress }));

      const row = findRow(renderer, FILE_A.name);
      const preventDefault = jest.fn();
      act(() => {
        row.props.onContextMenu({ preventDefault });
      });

      expect(preventDefault).toHaveBeenCalled();
      expect(onEntryLongPress).toHaveBeenCalledWith(FILE_A);
      Platform.OS = 'ios';
    });

    it('is not wired up at all when onEntryLongPress is omitted', () => {
      const renderer = render(createElement(FileList, { entries: [FILE_A], onPressEntry: jest.fn() }));

      const row = findRow(renderer, FILE_A.name);
      expect(row.props.onLongPress).toBeUndefined();
      expect(row.props.onContextMenu).toBeUndefined();
    });
  });

  // Issue #124: image entries get a preview thumbnail instead of the
  // generic icon; every other entry (dirs, non-image files) is unaffected.
  // Same "composite vs. host both match a bare testID lookup" shape as
  // `findRow` above — `onLoad` only survives on the actual `Image` element,
  // so filtering on it (rather than `onPress`) uniquely picks that one out.
  function findThumbnail(renderer: ReactTestRenderer) {
    const candidates = renderer.root.findAllByProps({ testID: 'file-thumbnail' });
    const thumbnail = candidates.find((node) => typeof node.props.onLoad === 'function');
    if (!thumbnail) throw new Error('no thumbnail Image found');
    return thumbnail;
  }

  describe('image thumbnails (issue #124)', () => {
    it('renders a thumbnail for an image entry', () => {
      const renderer = render(createElement(FileList, { entries: [IMAGE_FILE], onPressEntry: jest.fn() }));

      expect(findThumbnail(renderer)).toBeTruthy();
    });

    it('does not render a thumbnail for a non-image file or a directory', () => {
      const renderer = render(createElement(FileList, { entries: [DIR_A, FILE_A], onPressEntry: jest.fn() }));

      expect(renderer.root.findAllByProps({ testID: 'file-thumbnail' })).toHaveLength(0);
    });

    it('falls back to the generic image icon if the thumbnail fails to load', () => {
      const renderer = render(createElement(FileList, { entries: [IMAGE_FILE], onPressEntry: jest.fn() }));

      act(() => {
        findThumbnail(renderer).props.onError();
      });

      expect(renderer.root.findAllByProps({ testID: 'file-thumbnail' })).toHaveLength(0);
      // Still a row for the entry, just rendering the fallback icon instead.
      expect(findRow(renderer, IMAGE_FILE.name)).toBeTruthy();
    });

    it('still routes taps/long-presses through the same row Pressable as any other entry', () => {
      const onPressEntry = jest.fn();
      const renderer = render(createElement(FileList, { entries: [IMAGE_FILE], onPressEntry }));

      const row = findRow(renderer, IMAGE_FILE.name);
      act(() => {
        row.props.onPress();
      });

      expect(onPressEntry).toHaveBeenCalledWith(IMAGE_FILE);
    });
  });

  // Issue #125: video entries get a poster-frame thumbnail (server-side
  // `ffmpeg`-generated, `GET /api/media/thumbnail`) instead of the generic
  // `videocam-outline` icon — same shape as issue #124's image thumbnails
  // above, just a separate `testID` (`video-thumbnail`) since it's a
  // distinct `<Image>` element pointed at a different URL.
  function findVideoThumbnail(renderer: ReactTestRenderer) {
    const candidates = renderer.root.findAllByProps({ testID: 'video-thumbnail' });
    const thumbnail = candidates.find((node) => typeof node.props.onLoad === 'function');
    if (!thumbnail) throw new Error('no video thumbnail Image found');
    return thumbnail;
  }

  describe('video thumbnails (issue #125)', () => {
    it('renders a thumbnail for a video entry', () => {
      const renderer = render(createElement(FileList, { entries: [VIDEO_FILE], onPressEntry: jest.fn() }));

      expect(findVideoThumbnail(renderer)).toBeTruthy();
    });

    it('does not render a video thumbnail for a non-video file, an image, or a directory', () => {
      const renderer = render(
        createElement(FileList, { entries: [DIR_A, FILE_A, IMAGE_FILE], onPressEntry: jest.fn() }),
      );

      expect(renderer.root.findAllByProps({ testID: 'video-thumbnail' })).toHaveLength(0);
    });

    it('falls back to the generic video icon if the thumbnail fails to load', () => {
      const renderer = render(createElement(FileList, { entries: [VIDEO_FILE], onPressEntry: jest.fn() }));

      act(() => {
        findVideoThumbnail(renderer).props.onError();
      });

      expect(renderer.root.findAllByProps({ testID: 'video-thumbnail' })).toHaveLength(0);
      // Still a row for the entry, just rendering the fallback icon instead.
      expect(findRow(renderer, VIDEO_FILE.name)).toBeTruthy();
    });

    it('still routes taps/long-presses through the same row Pressable as any other entry', () => {
      const onPressEntry = jest.fn();
      const renderer = render(createElement(FileList, { entries: [VIDEO_FILE], onPressEntry }));

      const row = findRow(renderer, VIDEO_FILE.name);
      act(() => {
        row.props.onPress();
      });

      expect(onPressEntry).toHaveBeenCalledWith(VIDEO_FILE);
    });
  });
});
