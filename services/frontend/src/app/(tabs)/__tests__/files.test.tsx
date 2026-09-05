import { createElement } from 'react';
import { Text as RNText } from 'react-native';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import type { FileEntry } from '@/lib/files';

// Same mocking shape as `chat/__tests__/index.test.tsx` — plus
// `useLocalSearchParams` / `setParams` for M9-03 `?path=` sync.
const mockPush = jest.fn();
const mockSetParams = jest.fn();
let mockSearchParams: { path?: string } = {};
jest.mock('expo-router', () => {
  const ReactActual = jest.requireActual('react');
  return {
    useRouter: () => ({ push: mockPush, setParams: mockSetParams }),
    useLocalSearchParams: () => mockSearchParams,
    useFocusEffect: (callback: () => void | (() => void)) => {
      ReactActual.useEffect(() => {
        const cleanup = callback();
        return typeof cleanup === 'function' ? cleanup : undefined;
      }, [callback]);
    },
  };
});

// eslint-disable-next-line import/first -- must follow the jest.mock call above
import FilesScreen from '../files';

const VIDEO_FILE: FileEntry = {
  name: 'clip.mp4',
  path: 'clip.mp4',
  type: 'file',
  size: 2048,
  mtime: '2026-08-30T10:00:00.000Z',
  mime: 'video/mp4',
};
const TEXT_FILE: FileEntry = {
  name: 'notes.txt',
  path: 'notes.txt',
  type: 'file',
  size: 512,
  mtime: '2026-08-30T10:00:00.000Z',
  mime: 'text/plain',
};
const NOTES_DIR: FileEntry = {
  name: 'notes',
  path: 'notes',
  type: 'dir',
  size: 0,
  mtime: '2026-08-30T10:00:00.000Z',
  mime: null,
};
const LINK_TEST_FILE: FileEntry = {
  name: 'link-test.md',
  path: 'notes/link-test.md',
  type: 'file',
  size: 12,
  mtime: '2026-08-30T10:00:00.000Z',
  mime: 'text/markdown',
};

/** Mocks `GET /api/files` (the only call this screen's initial render
 * makes) — same "route `global.fetch` by method" shape as
 * `chat/__tests__/index.test.tsx`'s `mockThreadsApi`, simplified to just
 * the one GET this suite needs. */
function mockFilesApi(entries: FileEntry[]): void {
  const fetchMock = jest.fn(async () => ({
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => ({ path: '', entries }),
  }));
  global.fetch = fetchMock as unknown as typeof fetch;
}

function mockFilesApiByPath(listings: Record<string, FileEntry[] | 'missing'>): void {
  const fetchMock = jest.fn(async (input: RequestInfo) => {
    const url = typeof input === 'string' ? input : String(input);
    const query = new URL(url, 'http://localhost').searchParams.get('path') ?? '';
    const listing = Object.prototype.hasOwnProperty.call(listings, query) ? listings[query] : 'missing';
    if (listing === 'missing') {
      return {
        ok: false,
        status: 404,
        statusText: 'Not Found',
        json: async () => ({ detail: `directory '${query}' not found` }),
      };
    }
    return {
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => ({ path: query, entries: listing }),
    };
  });
  global.fetch = fetchMock as unknown as typeof fetch;
}

function toastText(renderer: ReactTestRenderer): string {
  const toast = renderer.root.findByProps({ testID: 'files-toast' });
  return toast
    .findAllByType(RNText)
    .map((node) => String(node.props.children ?? ''))
    .join('');
}

async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

let activeRenderer: ReactTestRenderer | null = null;

async function renderScreen(): Promise<ReactTestRenderer> {
  let renderer!: ReactTestRenderer;
  await act(async () => {
    renderer = create(createElement(FilesScreen));
  });
  await flush();
  activeRenderer = renderer;
  return renderer;
}

/** Same "find the actual `Pressable` row by its unique `onPress` prop"
 * helper as `components/__tests__/FileList.test.tsx`'s `findRow` — a
 * row's `accessibilityLabel`/`testID` are forwarded down to more than one
 * rendered node, but only the top-level `Pressable` itself carries
 * `onPress`. */
function findRow(renderer: ReactTestRenderer, name: string) {
  const candidates = renderer.root.findAllByProps({ accessibilityLabel: name });
  const row = candidates.find((node) => typeof node.props.onPress === 'function');
  if (!row) throw new Error(`no Pressable row found for "${name}"`);
  return row;
}

beforeEach(() => {
  mockPush.mockReset();
  mockSetParams.mockReset();
  mockSearchParams = {};
});

afterEach(() => {
  act(() => activeRenderer?.unmount());
  activeRenderer = null;
});

describe('FilesScreen — tap routing (M5-02)', () => {
  it('tapping a media-kind file navigates straight to the media modal, bypassing the action sheet', async () => {
    mockFilesApi([VIDEO_FILE]);

    const renderer = await renderScreen();
    const row = findRow(renderer, VIDEO_FILE.name);

    await act(async () => {
      row.props.onPress();
    });

    expect(mockPush).toHaveBeenCalledWith({
      pathname: '/media',
      params: { path: VIDEO_FILE.path, kind: 'video' },
    });
    // The action sheet never opens for this tap — its title (the entry's
    // own name) would otherwise be visible.
    expect(renderer.root.findAllByProps({ testID: 'file-action-sheet' })).toHaveLength(0);
  });

  it('tapping a non-media file opens the action sheet instead of navigating', async () => {
    mockFilesApi([TEXT_FILE]);

    const renderer = await renderScreen();
    const row = findRow(renderer, TEXT_FILE.name);

    await act(async () => {
      row.props.onPress();
    });

    expect(mockPush).not.toHaveBeenCalled();
    expect(renderer.root.findAllByProps({ testID: 'file-action-sheet' }).length).toBeGreaterThan(0);
  });

  it('long-press on a media file still opens the action sheet (not a media-bypass)', async () => {
    mockFilesApi([VIDEO_FILE]);

    const renderer = await renderScreen();
    const row = findRow(renderer, VIDEO_FILE.name);

    await act(async () => {
      row.props.onLongPress();
    });

    expect(mockPush).not.toHaveBeenCalled();
    expect(renderer.root.findAllByProps({ testID: 'file-action-sheet' }).length).toBeGreaterThan(0);
    // ...and that action sheet offers a "Play" action for this media file.
    expect(renderer.root.findAllByProps({ testID: 'file-action-play' }).length).toBeGreaterThan(0);
  });

  it('the action sheet\'s "Play" action navigates to the same media route as a direct tap', async () => {
    mockFilesApi([VIDEO_FILE]);

    const renderer = await renderScreen();
    const row = findRow(renderer, VIDEO_FILE.name);
    await act(async () => {
      row.props.onLongPress();
    });

    const playActions = renderer.root.findAllByProps({ testID: 'file-action-play' });
    const playAction = playActions.find((node) => typeof node.props.onPress === 'function');
    if (!playAction) throw new Error('no Pressable found for the "Play" action');
    await act(async () => {
      playAction.props.onPress();
    });

    expect(mockPush).toHaveBeenCalledWith({
      pathname: '/media',
      params: { path: VIDEO_FILE.path, kind: 'video' },
    });
  });

  it('the action sheet has no "Play" action for a non-media file', async () => {
    mockFilesApi([TEXT_FILE]);

    const renderer = await renderScreen();
    const row = findRow(renderer, TEXT_FILE.name);
    await act(async () => {
      row.props.onLongPress();
    });

    expect(renderer.root.findAllByProps({ testID: 'file-action-play' })).toHaveLength(0);
  });
});

describe('FilesScreen — ?path= deep link (M9-03)', () => {
  it('opens a directory path param and lists its entries', async () => {
    mockSearchParams = { path: 'notes' };
    mockFilesApiByPath({
      notes: [LINK_TEST_FILE],
      '': [NOTES_DIR],
    });

    const renderer = await renderScreen();

    expect(findRow(renderer, LINK_TEST_FILE.name)).toBeTruthy();
    expect(renderer.root.findAllByProps({ testID: 'file-entry-highlighted' })).toHaveLength(0);
  });

  it('opens a file path param at its parent and highlights the entry', async () => {
    mockSearchParams = { path: 'notes/link-test.md' };
    mockFilesApiByPath({
      'notes/link-test.md': 'missing',
      notes: [LINK_TEST_FILE],
      '': [NOTES_DIR],
    });

    const renderer = await renderScreen();

    const highlighted = renderer.root
      .findAllByProps({ testID: 'file-entry-highlighted' })
      .find((node) => typeof node.props.onPress === 'function');
    expect(highlighted?.props.accessibilityLabel).toBe(LINK_TEST_FILE.name);
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('does not auto-open the media player when deep-linking to a media file', async () => {
    mockSearchParams = { path: VIDEO_FILE.path };
    mockFilesApiByPath({
      [VIDEO_FILE.path]: 'missing',
      '': [VIDEO_FILE],
    });

    const renderer = await renderScreen();

    const highlighted = renderer.root
      .findAllByProps({ testID: 'file-entry-highlighted' })
      .find((node) => typeof node.props.onPress === 'function');
    expect(highlighted?.props.accessibilityLabel).toBe(VIDEO_FILE.name);
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('stays on the current listing and toasts when the path is missing', async () => {
    mockFilesApiByPath({
      '': [NOTES_DIR, TEXT_FILE],
      notes: [LINK_TEST_FILE],
    });

    const renderer = await renderScreen();
    expect(findRow(renderer, NOTES_DIR.name)).toBeTruthy();

    mockSearchParams = { path: 'nope/missing.txt' };
    await act(async () => {
      renderer.update(createElement(FilesScreen));
    });
    await flush();
    await flush();

    expect(toastText(renderer)).toBe('File not found: nope/missing.txt');
    expect(findRow(renderer, NOTES_DIR.name)).toBeTruthy();
    expect(findRow(renderer, TEXT_FILE.name)).toBeTruthy();
  });

  it('syncs a directory tap to the URL via setParams', async () => {
    mockFilesApiByPath({
      '': [NOTES_DIR, TEXT_FILE],
      notes: [LINK_TEST_FILE],
    });

    const renderer = await renderScreen();
    const row = findRow(renderer, NOTES_DIR.name);

    await act(async () => {
      row.props.onPress();
    });

    expect(mockSetParams).toHaveBeenCalledWith({ path: NOTES_DIR.path });
  });
});
