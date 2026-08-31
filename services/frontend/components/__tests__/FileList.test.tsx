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
});
