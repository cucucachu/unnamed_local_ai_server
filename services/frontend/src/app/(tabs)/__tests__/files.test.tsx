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
  path: '/personal/clip.mp4',
  type: 'file',
  size: 2048,
  mtime: '2026-08-30T10:00:00.000Z',
  mime: 'video/mp4',
};
const TEXT_FILE: FileEntry = {
  name: 'notes.txt',
  path: '/personal/notes.txt',
  type: 'file',
  size: 512,
  mtime: '2026-08-30T10:00:00.000Z',
  mime: 'text/plain',
};
const IMAGE_FILE: FileEntry = {
  name: 'photo.png',
  path: '/personal/photo.png',
  type: 'file',
  size: 4096,
  mtime: '2026-08-30T10:00:00.000Z',
  mime: 'image/png',
};
const NOTES_DIR: FileEntry = {
  name: 'notes',
  path: '/personal/notes',
  type: 'dir',
  size: 0,
  mtime: '2026-08-30T10:00:00.000Z',
  mime: null,
};
const LINK_TEST_FILE: FileEntry = {
  name: 'link-test.md',
  path: '/personal/notes/link-test.md',
  type: 'file',
  size: 12,
  mtime: '2026-08-30T10:00:00.000Z',
  mime: 'text/markdown',
};
const PERSONAL_SPACE: FileEntry = {
  name: 'personal',
  path: '/personal',
  type: 'dir',
  size: 0,
  mtime: '2026-08-30T10:00:00.000Z',
  mime: null,
  label: 'Personal',
  role: 'owner',
};
const SHARED_SPACES: FileEntry = { ...PERSONAL_SPACE, name: 'spaces', path: '/spaces', label: 'Shared spaces', role: null };
const FAMILY_SPACE: FileEntry = { ...PERSONAL_SPACE, name: 'family', path: '/spaces/family', label: 'Family', role: 'viewer' };

interface Dir {
  entries: FileEntry[];
  writable?: boolean;
  label?: string | null;
}

/** Routes `global.fetch` like the platform files API: `GET …/files?path=`
 * lists a known directory, `GET …/files/stat?path=` stats a known directory
 * or a file listed in one, anything else is a 404. A plain entry array is a
 * writable personal-space directory. */
function mockFilesApiByPath(dirs: Record<string, FileEntry[] | Dir>): jest.Mock {
  const known = (path: string): Dir | undefined => {
    const dir = Object.prototype.hasOwnProperty.call(dirs, path) ? dirs[path] : undefined;
    return Array.isArray(dir) ? { entries: dir } : dir;
  };
  const fetchMock = jest.fn(async (input: RequestInfo) => {
    const url = new URL(typeof input === 'string' ? input : String(input), 'http://localhost');
    const path = url.searchParams.get('path') ?? '';
    const ok = (body: unknown) => ({ ok: true, status: 200, statusText: 'OK', json: async () => body });
    const dir = known(path);
    if (url.pathname.endsWith('/stat')) {
      const file = Object.values(dirs)
        .flatMap((d) => (Array.isArray(d) ? d : d.entries))
        .find((e) => e.path === path);
      if (dir) return ok({ entry: { name: path.split('/').pop(), path, type: 'dir' }, role: 'owner', writable: true });
      if (file) return ok({ entry: file, role: 'owner', writable: true });
    } else if (dir) {
      const root = path === '/' || path === '/spaces';
      return ok({
        path,
        entries: dir.entries,
        role: root ? null : 'owner',
        writable: dir.writable ?? !root,
        space_label: dir.label !== undefined ? dir.label : root ? null : 'Personal',
      });
    }
    return { ok: false, status: 404, statusText: 'Not Found', json: async () => ({ detail: 'not_found' }) };
  });
  global.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

/** One writable directory, `/personal`, holding `entries` — where the
 * tap-routing tests start. */
function mockFilesApi(entries: FileEntry[]): void {
  mockSearchParams = { path: '/personal' };
  mockFilesApiByPath({ '/personal': entries });
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

describe('FilesScreen — image tap routing (issue #124)', () => {
  it('tapping an image file navigates straight to the media modal with kind "image"', async () => {
    mockFilesApi([IMAGE_FILE]);

    const renderer = await renderScreen();
    const row = findRow(renderer, IMAGE_FILE.name);

    await act(async () => {
      row.props.onPress();
    });

    expect(mockPush).toHaveBeenCalledWith({
      pathname: '/media',
      params: { path: IMAGE_FILE.path, kind: 'image' },
    });
    // Bypasses the action sheet, same as the existing video-tap behavior.
    expect(renderer.root.findAllByProps({ testID: 'file-action-sheet' })).toHaveLength(0);
  });

  it('long-press on an image file opens the action sheet with a "View" action (not "Play")', async () => {
    mockFilesApi([IMAGE_FILE]);

    const renderer = await renderScreen();
    const row = findRow(renderer, IMAGE_FILE.name);

    await act(async () => {
      row.props.onLongPress();
    });

    expect(renderer.root.findAllByProps({ testID: 'file-action-view' }).length).toBeGreaterThan(0);
    expect(renderer.root.findAllByProps({ testID: 'file-action-play' })).toHaveLength(0);
  });

  it('the action sheet\'s "View" action navigates to the same media route as a direct tap', async () => {
    mockFilesApi([IMAGE_FILE]);

    const renderer = await renderScreen();
    const row = findRow(renderer, IMAGE_FILE.name);
    await act(async () => {
      row.props.onLongPress();
    });

    const viewActions = renderer.root.findAllByProps({ testID: 'file-action-view' });
    const viewAction = viewActions.find((node) => typeof node.props.onPress === 'function');
    if (!viewAction) throw new Error('no Pressable found for the "View" action');
    await act(async () => {
      viewAction.props.onPress();
    });

    expect(mockPush).toHaveBeenCalledWith({
      pathname: '/media',
      params: { path: IMAGE_FILE.path, kind: 'image' },
    });
  });
});

describe('FilesScreen — ?path= deep link (M9-03)', () => {
  it('opens a directory path param and lists its entries', async () => {
    mockSearchParams = { path: '/personal/notes' };
    mockFilesApiByPath({ '/personal/notes': [LINK_TEST_FILE], '/personal': [NOTES_DIR] });

    const renderer = await renderScreen();

    expect(findRow(renderer, LINK_TEST_FILE.name)).toBeTruthy();
    expect(renderer.root.findAllByProps({ testID: 'file-entry-highlighted' })).toHaveLength(0);
  });

  it('opens a file path param at its parent and highlights the entry', async () => {
    mockSearchParams = { path: '/personal/notes/link-test.md' };
    mockFilesApiByPath({ '/personal/notes': [LINK_TEST_FILE], '/personal': [NOTES_DIR] });

    const renderer = await renderScreen();

    const highlighted = renderer.root
      .findAllByProps({ testID: 'file-entry-highlighted' })
      .find((node) => typeof node.props.onPress === 'function');
    expect(highlighted?.props.accessibilityLabel).toBe(LINK_TEST_FILE.name);
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('reads a pre-spaces relative path as a path in the personal space', async () => {
    mockSearchParams = { path: 'notes/link-test.md' };
    mockFilesApiByPath({ '/personal/notes': [LINK_TEST_FILE], '/personal': [NOTES_DIR] });

    const renderer = await renderScreen();

    const highlighted = renderer.root
      .findAllByProps({ testID: 'file-entry-highlighted' })
      .find((node) => typeof node.props.onPress === 'function');
    expect(highlighted?.props.accessibilityLabel).toBe(LINK_TEST_FILE.name);
  });

  it('does not auto-open the media player when deep-linking to a media file', async () => {
    mockSearchParams = { path: VIDEO_FILE.path };
    mockFilesApiByPath({ '/personal': [VIDEO_FILE] });

    const renderer = await renderScreen();

    const highlighted = renderer.root
      .findAllByProps({ testID: 'file-entry-highlighted' })
      .find((node) => typeof node.props.onPress === 'function');
    expect(highlighted?.props.accessibilityLabel).toBe(VIDEO_FILE.name);
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('stays on the current listing and toasts when the path is missing', async () => {
    mockSearchParams = { path: '/personal' };
    mockFilesApiByPath({ '/personal': [NOTES_DIR, TEXT_FILE], '/personal/notes': [LINK_TEST_FILE] });

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
    mockSearchParams = { path: '/personal' };
    mockFilesApiByPath({ '/personal': [NOTES_DIR, TEXT_FILE], '/personal/notes': [LINK_TEST_FILE] });

    const renderer = await renderScreen();
    const row = findRow(renderer, NOTES_DIR.name);

    await act(async () => {
      row.props.onPress();
    });

    expect(mockSetParams).toHaveBeenCalledWith({ path: NOTES_DIR.path });
  });
});

function crumbLabels(renderer: ReactTestRenderer): string[] {
  return renderer.root
    .findAllByProps({ testID: 'breadcrumb-segment' })
    .filter((node) => typeof node.props.onPress === 'function')
    .map((node) => String(node.props.accessibilityLabel));
}

describe('FilesScreen — spaces (M11-01)', () => {
  it('shows Personal and each shared space at the root, with no write actions', async () => {
    mockFilesApiByPath({ '/': [PERSONAL_SPACE, SHARED_SPACES], '/spaces': [FAMILY_SPACE] });

    const renderer = await renderScreen();

    expect(findRow(renderer, 'Personal')).toBeTruthy();
    expect(findRow(renderer, 'Family')).toBeTruthy();
    expect(renderer.root.findAllByProps({ accessibilityLabel: 'Shared spaces' })).toHaveLength(0);
    expect(crumbLabels(renderer)).toEqual(['Home']);
    expect(renderer.root.findAllByProps({ testID: 'files-upload-button' })).toHaveLength(0);
    expect(renderer.root.findAllByProps({ testID: 'files-read-only' })).toHaveLength(0);
    // Spaces themselves have no action sheet.
    expect(findRow(renderer, 'Family').props.onLongPress).toBeUndefined();

    await act(async () => {
      findRow(renderer, 'Family').props.onPress();
    });
    expect(mockSetParams).toHaveBeenCalledWith({ path: '/spaces/family' });
  });

  it('breadcrumbs name the space and go through it, not through /spaces', async () => {
    mockSearchParams = { path: '/spaces/family/trip' };
    mockFilesApiByPath({ '/spaces/family/trip': { entries: [], label: 'Family' } });

    const renderer = await renderScreen();

    expect(crumbLabels(renderer)).toEqual(['Home', 'Family', 'trip']);
    const familyCrumb = renderer.root
      .findAllByProps({ testID: 'breadcrumb-segment', accessibilityLabel: 'Family' })
      .find((node) => typeof node.props.onPress === 'function');
    await act(async () => {
      familyCrumb?.props.onPress();
    });
    expect(mockSetParams).toHaveBeenCalledWith({ path: '/spaces/family' });
  });

  it('a view-only space hides upload, new folder, rename, move, copy and delete', async () => {
    const photo = { ...IMAGE_FILE, path: '/spaces/family/photo.png' };
    const doc = { ...TEXT_FILE, path: '/spaces/family/notes.txt' };
    mockSearchParams = { path: '/spaces/family' };
    mockFilesApiByPath({ '/spaces/family': { entries: [photo, doc], writable: false, label: 'Family' } });

    const renderer = await renderScreen();

    expect(renderer.root.findAllByProps({ testID: 'files-upload-button' })).toHaveLength(0);
    expect(renderer.root.findAllByProps({ testID: 'files-new-folder-button' })).toHaveLength(0);
    expect(renderer.root.findAllByProps({ testID: 'files-read-only' }).length).toBeGreaterThan(0);

    await act(async () => {
      findRow(renderer, doc.name).props.onLongPress();
    });
    for (const action of ['rename', 'move', 'copy', 'delete']) {
      expect(renderer.root.findAllByProps({ testID: `file-action-${action}` })).toHaveLength(0);
    }
    expect(renderer.root.findAllByProps({ testID: 'file-action-download' }).length).toBeGreaterThan(0);
  });

  it('an editable space keeps every action', async () => {
    mockFilesApi([TEXT_FILE]);

    const renderer = await renderScreen();

    expect(renderer.root.findAllByProps({ testID: 'files-upload-button' }).length).toBeGreaterThan(0);
    expect(crumbLabels(renderer)).toEqual(['Home', 'Personal']);
    await act(async () => {
      findRow(renderer, TEXT_FILE.name).props.onLongPress();
    });
    for (const action of ['download', 'rename', 'move', 'copy', 'delete']) {
      expect(renderer.root.findAllByProps({ testID: `file-action-${action}` }).length).toBeGreaterThan(0);
    }
  });
});
