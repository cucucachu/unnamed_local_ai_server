import { ApiError } from '../api';
import {
  copyPath,
  deletePath,
  describeFilesError,
  joinPath,
  listFiles,
  mkdir,
  movePath,
  parentPath,
  statPath,
  uploadToDir,
  type UploadPart,
} from '../files';

function mockFetchOk(body: unknown, status = 200): jest.Mock {
  const fetchMock = jest.fn().mockResolvedValue({
    ok: true,
    status,
    statusText: 'OK',
    json: async () => body,
  });
  global.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

afterEach(() => {
  jest.clearAllMocks();
});

describe('joinPath', () => {
  const cases: { name: string; dir: string; entryName: string; expected: string }[] = [
    { name: 'joining onto the root gives a top-level path', dir: '/', entryName: 'personal', expected: '/personal' },
    {
      name: 'joining onto a nested dir inserts exactly one slash',
      dir: '/personal/a/b',
      entryName: 'c.txt',
      expected: '/personal/a/b/c.txt',
    },
    { name: 'a trailing slash on the dir is not doubled', dir: '/spaces/fam/', entryName: 'x', expected: '/spaces/fam/x' },
  ];
  for (const { name, dir, entryName, expected } of cases) {
    it(name, () => {
      expect(joinPath(dir, entryName)).toBe(expected);
    });
  }
});

describe('parentPath', () => {
  const cases: { name: string; path: string; expected: string }[] = [
    { name: 'a space root has the root as its parent', path: '/personal', expected: '/' },
    { name: 'the root is its own parent', path: '/', expected: '/' },
    { name: 'a shared space sits under /spaces', path: '/spaces/fam', expected: '/spaces' },
    { name: 'a nested entry strips only its own last segment', path: '/personal/a/b/c.txt', expected: '/personal/a/b' },
  ];
  for (const { name, path, expected } of cases) {
    it(name, () => {
      expect(parentPath(path)).toBe(expected);
    });
  }
});

describe('listFiles', () => {
  const cases: { name: string; path: string }[] = [
    { name: 'a space root', path: '/personal' },
    { name: 'a shared space dir', path: '/spaces/family/docs' },
    { name: 'a path containing a space', path: '/personal/my docs' },
    { name: 'a path with a non-ASCII (Cyrillic) name', path: '/personal/тест файл' },
  ];

  for (const { name, path } of cases) {
    it(`GETs /api/platform/files with the path correctly URL-encoded — ${name}`, async () => {
      const fetchMock = mockFetchOk({ path, entries: [], role: 'owner', writable: true });

      await listFiles(path);

      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [calledUrl] = fetchMock.mock.calls[0];
      expect(calledUrl).toContain(`/api/platform/files?path=${encodeURIComponent(path)}`);
    });
  }

  it('shows the root as Personal plus each shared space (no "spaces" folder)', async () => {
    const dir = (name: string, path: string, label: string) => ({
      name, path, type: 'dir', size: 0, mtime: '2026-01-01T00:00:00Z', mime: null, label,
    });
    const fetchMock = jest.fn().mockImplementation(async (url: string) => ({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () =>
        url.includes(`path=${encodeURIComponent('/spaces')}`)
          ? { path: '/spaces', entries: [dir('family', '/spaces/family', 'Family')], role: null, writable: false }
          : {
              path: '/',
              entries: [dir('personal', '/personal', 'Personal'), dir('spaces', '/spaces', 'Shared spaces')],
              role: null,
              writable: false,
            },
    }));
    global.fetch = fetchMock as unknown as typeof fetch;

    const listing = await listFiles('/');

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(listing.path).toBe('/');
    expect(listing.writable).toBe(false);
    expect(listing.entries.map((e) => e.path)).toEqual(['/personal', '/spaces/family']);
  });

  it('returns the parsed listing on success', async () => {
    mockFetchOk({
      path: '/personal/docs',
      entries: [{ name: 'a.txt', path: '/personal/docs/a.txt', type: 'file', size: 3, mtime: '2026-01-01T00:00:00Z', mime: 'text/plain' }],
      role: 'owner',
      writable: true,
    });

    const result = await listFiles('/personal/docs');

    expect(result.path).toBe('/personal/docs');
    expect(result.entries).toHaveLength(1);
  });

  it('throws ApiError with the server detail on a 404 (missing dir)', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      json: async () => ({ detail: 'not_found' }),
    }) as unknown as typeof fetch;

    const error = await listFiles('/personal/missing').catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, detail: 'not_found' });
  });
});

describe('statPath', () => {
  it('GETs /api/platform/files/stat with the encoded path', async () => {
    const fetchMock = mockFetchOk({ entry: { path: '/personal/a b.txt', type: 'file' }, role: 'owner', writable: true });

    const result = await statPath('/personal/a b.txt');

    const [calledUrl] = fetchMock.mock.calls[0];
    expect(calledUrl).toContain(`/api/platform/files/stat?path=${encodeURIComponent('/personal/a b.txt')}`);
    expect(result.entry.type).toBe('file');
  });
});

describe('describeFilesError', () => {
  it('turns API codes into readable text and passes anything else through', () => {
    expect(describeFilesError('insufficient_role')).toBe('You have view-only access here');
    expect(describeFilesError('something else')).toBe('something else');
  });
});

describe('mkdir', () => {
  it('POSTs a JSON body with the given path (unencoded — it is a body field, not a URL)', async () => {
    const fetchMock = mockFetchOk({ path: 'a b/новая папка' }, 201);

    await mkdir('a b/новая папка');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [calledUrl, calledInit] = fetchMock.mock.calls[0];
    expect(calledUrl).toContain('/api/platform/files/mkdir');
    expect(calledInit).toMatchObject({ method: 'POST' });
    expect(JSON.parse(calledInit.body)).toEqual({ path: 'a b/новая папка' });
  });
});

describe('movePath', () => {
  it('POSTs {src, dst} to /api/platform/files/move', async () => {
    const fetchMock = mockFetchOk({ src: 'a.txt', dst: 'b.txt' });

    await movePath('a.txt', 'b.txt');

    const [calledUrl, calledInit] = fetchMock.mock.calls[0];
    expect(calledUrl).toContain('/api/platform/files/move');
    expect(JSON.parse(calledInit.body)).toEqual({ src: 'a.txt', dst: 'b.txt' });
  });

  it('propagates a 409 (destination exists) as an ApiError', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      json: async () => ({ detail: "destination 'b.txt' already exists" }),
    }) as unknown as typeof fetch;

    const error = await movePath('a.txt', 'b.txt').catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
  });
});

describe('copyPath', () => {
  it('POSTs {src, dst} to /api/platform/files/copy', async () => {
    const fetchMock = mockFetchOk({ src: 'a.txt', dst: 'copy of a.txt' });

    await copyPath('a.txt', 'copy of a.txt');

    const [calledUrl, calledInit] = fetchMock.mock.calls[0];
    expect(calledUrl).toContain('/api/platform/files/copy');
    expect(JSON.parse(calledInit.body)).toEqual({ src: 'a.txt', dst: 'copy of a.txt' });
  });
});

describe('deletePath', () => {
  const cases: { name: string; path: string }[] = [
    { name: 'a simple name', path: 'a.txt' },
    { name: 'a name with a space', path: 'my file.txt' },
    { name: 'a non-ASCII name', path: 'тест файл.txt' },
  ];

  for (const { name, path } of cases) {
    it(`DELETEs /api/platform/files with the path correctly URL-encoded — ${name}`, async () => {
      const fetchMock = jest.fn().mockResolvedValue({
        ok: true,
        status: 204,
        statusText: 'No Content',
        json: async () => {
          throw new Error('no body');
        },
      });
      global.fetch = fetchMock as unknown as typeof fetch;

      await deletePath(path);

      const [calledUrl, calledInit] = fetchMock.mock.calls[0];
      expect(calledUrl).toContain(`/api/platform/files?path=${encodeURIComponent(path)}`);
      expect(calledInit).toMatchObject({ method: 'DELETE' });
    });
  }
});

describe('uploadToDir', () => {
  // `uploadToDir` is the WEB-ONLY upload path (see its docstring in
  // `lib/files.ts` for why — native uses `uploadOneNative`'s
  // `expo-file-system` `File.upload()` instead, not `fetch` + `FormData`,
  // due to a confirmed Expo SDK 57 bug in mixed string+file `FormData`
  // bodies). `uploadToDir` doesn't care what's inside a part beyond
  // handing it to `FormData.append` — so a minimal fake object is
  // sufficient here; this suite tests `uploadToDir`'s own request-shaping
  // (fields, encoding, response handling), not real `Blob` behavior.
  function fakePart(tag: string): UploadPart {
    return { tag } as unknown as UploadPart;
  }

  it('POSTs multipart form data to /api/platform/files/upload with the target dir as the "path" field', async () => {
    const fetchMock = mockFetchOk({ uploaded: ['docs/a.txt'] }, 201);

    await uploadToDir('docs', [fakePart('a.txt')]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [calledUrl, calledInit] = fetchMock.mock.calls[0];
    expect(calledUrl).toContain('/api/platform/files/upload');
    expect(calledInit.method).toBe('POST');

    const body = calledInit.body as FormData;
    expect(body.get('path')).toBe('docs');
    expect(body.getAll('file')).toHaveLength(1);
  });

  it('appends one "file" field per part, and a "path" field with a space/unicode target dir', async () => {
    const fetchMock = mockFetchOk({ uploaded: ['a b/тест.txt', 'a b/тест2.txt'] }, 201);

    await uploadToDir('a b', [fakePart('тест.txt'), fakePart('тест2.txt')]);

    const [, calledInit] = fetchMock.mock.calls[0];
    const body = calledInit.body as FormData;
    expect(body.get('path')).toBe('a b');
    expect(body.getAll('file')).toHaveLength(2);
  });

  it('returns the parsed {uploaded} result on success', async () => {
    mockFetchOk({ uploaded: ['docs/a.txt'] }, 201);

    const result = await uploadToDir('docs', [fakePart('a.txt')]);

    expect(result).toEqual({ uploaded: ['docs/a.txt'] });
  });

  it('throws ApiError on a non-2xx response', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      json: async () => ({ detail: "directory 'missing' not found" }),
    }) as unknown as typeof fetch;

    const error = await uploadToDir('missing', [fakePart('a.txt')]).catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).detail).toBe("directory 'missing' not found");
  });
});
