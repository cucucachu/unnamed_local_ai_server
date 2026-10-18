import { createElement } from 'react';
import { Alert, type AlertButton, Text as RNText } from 'react-native';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';

const mockBack = jest.fn();
const mockDismissTo = jest.fn();
const mockSetParams = jest.fn();
const mockDispatch = jest.fn();
let mockSearchParams: { path?: string } = {};
const mockListeners: Record<string, (event: unknown) => void> = {};
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, dismissTo: mockDismissTo, setParams: mockSetParams }),
  useLocalSearchParams: () => mockSearchParams,
  useNavigation: () => ({
    addListener: (name: string, listener: (event: unknown) => void) => {
      mockListeners[name] = listener;
      return () => delete mockListeners[name];
    },
    dispatch: mockDispatch,
  }),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock call above
import FileScreen from '../file';

interface Stored {
  bytes: Uint8Array;
  version: string;
  size?: number;
}

/** The platform's files API over an in-memory tree: stat, stream and write
 * (with its `if_version` check, unless `writeReply` scripts the answer). */
function mockFiles(files: Record<string, Stored>, { writable = true } = {}) {
  const writes: { path: string; content: string; if_version?: string }[] = [];
  let versions = 1;
  const fetchMock = jest.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input, 'http://localhost');
    const path = url.searchParams.get('path') ?? '';
    const json = (status: number, body: unknown) => ({
      ok: status < 300,
      status,
      statusText: '',
      headers: { get: () => null },
      json: async () => body,
    });
    if (url.pathname.endsWith('/files/stat')) {
      const file = files[path];
      if (!file) return json(404, { detail: 'not_found' });
      const name = path.split('/').pop();
      const size = file.size ?? file.bytes.length;
      return json(200, { entry: { name, path, type: 'file', size, mtime: '', mime: null, version: file.version }, role: writable ? 'editor' : 'viewer', writable });
    }
    if (url.pathname.endsWith('/files/stream')) {
      const file = files[path];
      return { ok: true, status: 200, statusText: '', arrayBuffer: async () => file.bytes.slice().buffer };
    }
    if (url.pathname.endsWith('/files/write')) {
      const body = JSON.parse(String(init?.body));
      writes.push(body);
      const current = files[body.path];
      if (body.if_version !== undefined && current?.version !== body.if_version) return json(409, { detail: 'changed' });
      versions += 1;
      files[body.path] = { bytes: new TextEncoder().encode(body.content), version: `v${versions}` };
      return json(200, { path: body.path, version: `v${versions}` });
    }
    throw new Error(`unexpected ${init?.method ?? 'GET'} ${input}`);
  });
  global.fetch = fetchMock as unknown as typeof fetch;
  return { fetchMock, writes };
}

const text = (s: string, version = 'v1'): Stored => ({ bytes: new TextEncoder().encode(s), version });

let renderer: ReactTestRenderer | null = null;

async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function renderFile(path: string): Promise<ReactTestRenderer> {
  mockSearchParams = { path };
  await act(async () => {
    renderer = create(createElement(FileScreen));
  });
  await flush();
  return renderer!;
}

function byId(r: ReactTestRenderer, testID: string): ReactTestInstance {
  const [node] = r.root.findAll((n) => n.props.testID === testID);
  if (!node) throw new Error(`no ${testID}`);
  return node;
}

function exists(r: ReactTestRenderer, testID: string): boolean {
  return r.root.findAll((node) => node.props.testID === testID).length > 0;
}

async function press(r: ReactTestRenderer, testID: string): Promise<void> {
  const node = r.root.find((n) => n.props.testID === testID && typeof n.props.onPress === 'function');
  await act(async () => {
    node.props.onPress();
  });
  await flush();
}

async function typeInEditor(r: ReactTestRenderer, value: string): Promise<void> {
  const editor = r.root.find((n) => n.props.testID === 'file-editor' && typeof n.props.onChangeText === 'function');
  await act(async () => {
    editor.props.onChangeText(value);
  });
}

function textOf(r: ReactTestRenderer): string {
  return r.root
    .findAllByType(RNText)
    .map((node) => (Array.isArray(node.props.children) ? node.props.children.join('') : String(node.props.children ?? '')))
    .join(' | ');
}

beforeEach(() => {
  for (const fn of [mockBack, mockDismissTo, mockSetParams, mockDispatch]) fn.mockReset();
  for (const key of Object.keys(mockListeners)) delete mockListeners[key];
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
  jest.restoreAllMocks();
});

describe('FileScreen (M22-10)', () => {
  it('renders markdown; relative and file: links open in Files', async () => {
    mockFiles({ '/personal/notes/a.md': text('# Shopping\n\nSee [b](b.md), [up](../todo.md) and [c](file:/spaces/family/c.txt).\n') });
    const r = await renderFile('/personal/notes/a.md');

    expect(exists(r, 'markdown')).toBe(true);
    expect(textOf(r)).toContain('Shopping');
    const links = r.root.findAll(
      (n) => n.props.testID === 'file-link' && typeof n.props.onPress === 'function' && typeof n.type !== 'string',
    );
    for (const link of links) {
      await act(async () => link.props.onPress());
    }
    expect(mockDismissTo.mock.calls.map(([arg]) => arg.params.path)).toEqual([
      '/personal/notes/b.md',
      '/personal/todo.md',
      '/spaces/family/c.txt',
    ]);
  });

  it('shows other text in monospace, and no Edit for a viewer', async () => {
    mockFiles({ '/spaces/family/list.txt': text('milk\neggs\n') }, { writable: false });
    const r = await renderFile('/spaces/family/list.txt');

    expect(byId(r, 'file-text').props.children).toBe('milk\neggs\n');
    expect(exists(r, 'markdown')).toBe(false);
    expect(exists(r, 'file-edit')).toBe(false);
  });

  it('refuses binary files and files over 1 MiB', async () => {
    const { fetchMock } = mockFiles({
      '/personal/blob.txt': { bytes: new Uint8Array([0xff, 0xfe, 0x00, 0x41]), version: 'v1' },
      '/personal/huge.log': { bytes: new Uint8Array(0), version: 'v1', size: 2 * 1024 * 1024 },
    });
    let r = await renderFile('/personal/blob.txt');
    expect(textOf(r)).toContain("This file isn't text.");

    act(() => r.unmount());
    r = await renderFile('/personal/huge.log');
    expect(textOf(r)).toContain('too large to open here');
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/stream?path=%2Fpersonal%2Fhuge.log'))).toBe(false);
  });

  it('edits and saves with the version it loaded, then the new one', async () => {
    const { writes } = mockFiles({ '/personal/n.md': text('one\n') });
    const r = await renderFile('/personal/n.md');

    await press(r, 'file-edit');
    expect(byId(r, 'file-editor').props.value).toBe('one\n');
    await typeInEditor(r, 'two\n');
    await press(r, 'file-save');

    expect(writes).toEqual([{ path: '/personal/n.md', content: 'two\n', if_version: 'v1' }]);
    expect(exists(r, 'file-editor')).toBe(false);
    expect(textOf(r)).toContain('Saved.');
    expect(textOf(r)).toContain('two');

    await press(r, 'file-edit');
    await typeInEditor(r, 'three\n');
    await press(r, 'file-save');
    expect(writes[1]).toEqual({ path: '/personal/n.md', content: 'three\n', if_version: 'v2' });
  });

  it('on a conflict, keep mine overwrites', async () => {
    const files = { '/personal/n.md': text('one\n') };
    const { writes } = mockFiles(files);
    const r = await renderFile('/personal/n.md');
    await press(r, 'file-edit');
    files['/personal/n.md'] = text('theirs\n', 'v9');
    await typeInEditor(r, 'mine\n');
    await press(r, 'file-save');

    expect(exists(r, 'file-conflict')).toBe(true);
    expect(byId(r, 'file-editor').props.value).toBe('mine\n');
    await press(r, 'file-conflict-overwrite');

    expect(writes.map((w) => w.if_version)).toEqual(['v1', undefined]);
    expect(new TextDecoder().decode(files['/personal/n.md'].bytes)).toBe('mine\n');
    expect(exists(r, 'file-conflict')).toBe(false);
    expect(exists(r, 'file-editor')).toBe(false);
  });

  it('on a conflict, reload shows theirs and drops mine', async () => {
    const files = { '/personal/n.md': text('one\n') };
    mockFiles(files);
    const r = await renderFile('/personal/n.md');
    await press(r, 'file-edit');
    files['/personal/n.md'] = text('theirs\n', 'v9');
    await typeInEditor(r, 'mine\n');
    await press(r, 'file-save');
    await press(r, 'file-conflict-reload');

    expect(exists(r, 'file-editor')).toBe(false);
    expect(textOf(r)).toContain('theirs');
    await press(r, 'file-edit');
    expect(byId(r, 'file-editor').props.value).toBe('theirs\n');
  });

  it('on a conflict, save as copy writes mine next to theirs and opens it', async () => {
    const files: Record<string, Stored> = { '/personal/notes/n.md': text('one\n'), '/personal/notes/n (copy).md': text('old copy\n') };
    const { writes } = mockFiles(files);
    const r = await renderFile('/personal/notes/n.md');
    await press(r, 'file-edit');
    files['/personal/notes/n.md'] = text('theirs\n', 'v9');
    await typeInEditor(r, 'mine\n');
    await press(r, 'file-save');
    await press(r, 'file-conflict-copy');

    expect(writes[1]).toEqual({ path: '/personal/notes/n (copy 2).md', content: 'mine\n' });
    expect(new TextDecoder().decode(files['/personal/notes/n.md'].bytes)).toBe('theirs\n');
    expect(mockSetParams).toHaveBeenCalledWith({ path: '/personal/notes/n (copy 2).md' });
  });

  it('asks before leaving with unsaved changes', async () => {
    mockFiles({ '/personal/n.md': text('one\n') });
    const r = await renderFile('/personal/n.md');
    const leave = () => {
      const event = { preventDefault: jest.fn(), data: { action: { type: 'GO_BACK' } } };
      act(() => mockListeners.beforeRemove(event));
      return event;
    };

    await press(r, 'file-edit');
    expect(mockListeners.beforeRemove).toBeUndefined();
    await typeInEditor(r, 'changed\n');
    let answer: 'cancel' | 'destructive' = 'cancel';
    jest.spyOn(Alert, 'alert').mockImplementation((_t, _m, buttons?: AlertButton[]) => {
      buttons?.find((b) => b.style === answer)?.onPress?.();
    });
    expect(leave().preventDefault).toHaveBeenCalled();
    await flush();
    expect(mockDispatch).not.toHaveBeenCalled();

    answer = 'destructive';
    leave();
    await flush();
    expect(mockDispatch).toHaveBeenCalledWith({ type: 'GO_BACK' });
  });
});
