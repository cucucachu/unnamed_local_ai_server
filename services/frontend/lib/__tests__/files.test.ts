import { ApiError } from '../api';
import { copyPath, deletePath, joinPath, listFiles, mkdir, movePath, parentPath, uploadToDir, type UploadPart } from '../files';

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
    { name: 'joining onto the root has no leading slash', dir: '', entryName: 'a.txt', expected: 'a.txt' },
    { name: 'joining onto a nested dir inserts exactly one slash', dir: 'a/b', entryName: 'c.txt', expected: 'a/b/c.txt' },
  ];
  for (const { name, dir, entryName, expected } of cases) {
    it(name, () => {
      expect(joinPath(dir, entryName)).toBe(expected);
    });
  }
});

describe('parentPath', () => {
  const cases: { name: string; path: string; expected: string }[] = [
    { name: 'a root-level entry has an empty parent', path: 'a.txt', expected: '' },
    { name: 'a nested entry strips only its own last segment', path: 'a/b/c.txt', expected: 'a/b' },
  ];
  for (const { name, path, expected } of cases) {
    it(name, () => {
      expect(parentPath(path)).toBe(expected);
    });
  }
});

describe('listFiles', () => {
  const cases: { name: string; path: string }[] = [
    { name: 'the workspace root (empty path)', path: '' },
    { name: 'a simple nested dir', path: 'docs' },
    { name: 'a path containing a space', path: 'my docs' },
    { name: 'a path with multiple nested segments', path: 'a/b/c' },
    { name: 'a path with a non-ASCII (Cyrillic) name', path: 'тест файл' },
  ];

  for (const { name, path } of cases) {
    it(`GETs /api/files with the path correctly URL-encoded — ${name}`, async () => {
      const fetchMock = mockFetchOk({ path, entries: [] });

      await listFiles(path);

      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [calledUrl] = fetchMock.mock.calls[0];
      expect(calledUrl).toContain(`/api/files?path=${encodeURIComponent(path)}`);
    });
  }

  it('returns the parsed listing on success', async () => {
    mockFetchOk({ path: 'docs', entries: [{ name: 'a.txt', path: 'docs/a.txt', type: 'file', size: 3, mtime: '2026-01-01T00:00:00Z', mime: 'text/plain' }] });

    const result = await listFiles('docs');

    expect(result.path).toBe('docs');
    expect(result.entries).toHaveLength(1);
  });

  it('throws ApiError with the server detail on a 404 (missing dir)', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      json: async () => ({ detail: "directory 'missing' not found" }),
    }) as unknown as typeof fetch;

    const error = await listFiles('missing').catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, detail: "directory 'missing' not found" });
  });
});

describe('mkdir', () => {
  it('POSTs a JSON body with the given path (unencoded — it is a body field, not a URL)', async () => {
    const fetchMock = mockFetchOk({ path: 'a b/новая папка' }, 201);

    await mkdir('a b/новая папка');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [calledUrl, calledInit] = fetchMock.mock.calls[0];
    expect(calledUrl).toContain('/api/files/mkdir');
    expect(calledInit).toMatchObject({ method: 'POST' });
    expect(JSON.parse(calledInit.body)).toEqual({ path: 'a b/новая папка' });
  });
});

describe('movePath', () => {
  it('POSTs {src, dst} to /api/files/move', async () => {
    const fetchMock = mockFetchOk({ src: 'a.txt', dst: 'b.txt' });

    await movePath('a.txt', 'b.txt');

    const [calledUrl, calledInit] = fetchMock.mock.calls[0];
    expect(calledUrl).toContain('/api/files/move');
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
  it('POSTs {src, dst} to /api/files/copy', async () => {
    const fetchMock = mockFetchOk({ src: 'a.txt', dst: 'copy of a.txt' });

    await copyPath('a.txt', 'copy of a.txt');

    const [calledUrl, calledInit] = fetchMock.mock.calls[0];
    expect(calledUrl).toContain('/api/files/copy');
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
    it(`DELETEs /api/files with the path correctly URL-encoded — ${name}`, async () => {
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
      expect(calledUrl).toContain(`/api/files?path=${encodeURIComponent(path)}`);
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

  it('POSTs multipart form data to /api/files/upload with the target dir as the "path" field', async () => {
    const fetchMock = mockFetchOk({ uploaded: ['docs/a.txt'] }, 201);

    await uploadToDir('docs', [fakePart('a.txt')]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [calledUrl, calledInit] = fetchMock.mock.calls[0];
    expect(calledUrl).toContain('/api/files/upload');
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
