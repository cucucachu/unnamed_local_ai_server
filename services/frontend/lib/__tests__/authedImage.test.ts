import { createElement } from 'react';
import { Platform } from 'react-native';
import { act, create } from 'react-test-renderer';

const mockWrites: { uri: string; bytes: Uint8Array }[] = [];

jest.mock('expo-file-system', () => {
  class Directory {
    uri: string;
    exists = true;
    constructor(...parts: unknown[]) {
      this.uri = parts.map((p) => (typeof p === 'string' ? p : (p as { uri: string }).uri)).join('/');
    }
    create() {}
    delete() {}
  }
  class File {
    uri: string;
    constructor(dir: { uri: string }, name: string) {
      this.uri = `${dir.uri}/${name}`;
    }
    write(bytes: Uint8Array) {
      mockWrites.push({ uri: this.uri, bytes });
    }
  }
  return { Directory, File, Paths: { cache: 'file:///cache' } };
});

import { setSessionToken } from '../session';
import { useAuthedImage } from '../authedImage';

const originalOS = Platform.OS;
const originalFetch = global.fetch;

function Probe({ url, seen }: { url: string; seen: { uri: string | null; failed: boolean }[] }) {
  seen.push(useAuthedImage(url));
  return null;
}

async function render(url: string) {
  const seen: { uri: string | null; failed: boolean }[] = [];
  await act(async () => {
    create(createElement(Probe, { url, seen }));
  });
  return seen;
}

describe('useAuthedImage', () => {
  afterEach(() => {
    Object.defineProperty(Platform, 'OS', {
      value: originalOS,
      configurable: true,
    });
    global.fetch = originalFetch;
    mockWrites.length = 0;
    setSessionToken(null);
  });

  it('on web, is the URL itself (the cookie rides along)', async () => {
    Object.defineProperty(Platform, 'OS', { value: 'web', configurable: true });
    global.fetch = jest.fn();
    const seen = await render('http://host/api/platform/files/stream?path=%2Fpersonal%2Fa.jpg');
    expect(seen.at(-1)).toEqual({
      uri: 'http://host/api/platform/files/stream?path=%2Fpersonal%2Fa.jpg',
      failed: false,
    });
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('on the phone, fetches with the bearer header into the cache and shows the file', async () => {
    Object.defineProperty(Platform, 'OS', {
      value: 'android',
      configurable: true,
    });
    setSessionToken('hs_abc');
    const fetchMock = jest.fn(async () => new Response(new Uint8Array([1, 2, 3]), { status: 200 }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const url = 'http://host/api/platform/files/stream?path=%2Fpersonal%2Fb.jpg';
    const seen = await render(url);
    expect(fetchMock).toHaveBeenCalledWith(url, {
      headers: { Authorization: 'Bearer hs_abc' },
    });
    expect(seen[0]).toEqual({ uri: null, failed: false });
    expect(mockWrites).toHaveLength(1);
    expect(Array.from(mockWrites[0].bytes)).toEqual([1, 2, 3]);
    expect(seen.at(-1)).toEqual({ uri: mockWrites[0].uri, failed: false });

    const again = await render(url);
    expect(again[0]).toEqual({ uri: mockWrites[0].uri, failed: false });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('on the phone, reports a failed fetch', async () => {
    Object.defineProperty(Platform, 'OS', {
      value: 'android',
      configurable: true,
    });
    global.fetch = jest.fn(async () => new Response('nope', { status: 404 })) as unknown as typeof fetch;
    const seen = await render('http://host/api/platform/files/stream?path=%2Fpersonal%2Fgone.jpg');
    expect(seen.at(-1)).toEqual({ uri: null, failed: true });
    expect(mockWrites).toHaveLength(0);
  });
});
