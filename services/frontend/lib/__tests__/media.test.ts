import { Platform } from 'react-native';

import { mediaKind, streamUrl } from '../media';

// `apiBase()` (`lib/api.ts`) is `Platform.OS`-dependent — `''` on web,
// otherwise `EXPO_PUBLIC_API_HOST ?? 'http://homeai.local'` — so `streamUrl`
// needs a pinned `Platform.OS` to assert an exact string. Same
// mutate-then-restore convention as `FileList.test.tsx`/`chat/index.test.tsx`
// (jest-expo's default `Platform.OS` is `'ios'`, restored after each test
// here so this file doesn't leak platform state into others).

describe('mediaKind', () => {
  const cases: { name: string; fileName: string; expected: ReturnType<typeof mediaKind> }[] = [
    { name: 'mp4 is video', fileName: 'clip.mp4', expected: 'video' },
    { name: 'mov is video', fileName: 'clip.mov', expected: 'video' },
    { name: 'm4v is video', fileName: 'clip.m4v', expected: 'video' },
    { name: 'webm is video', fileName: 'clip.webm', expected: 'video' },
    { name: 'mkv is video', fileName: 'clip.mkv', expected: 'video' },
    { name: 'mp3 is audio', fileName: 'song.mp3', expected: 'audio' },
    { name: 'm4a is audio', fileName: 'song.m4a', expected: 'audio' },
    { name: 'aac is audio', fileName: 'song.aac', expected: 'audio' },
    { name: 'wav is audio', fileName: 'song.wav', expected: 'audio' },
    { name: 'ogg is audio', fileName: 'song.ogg', expected: 'audio' },
    { name: 'flac is audio', fileName: 'song.flac', expected: 'audio' },
    { name: 'case-insensitive: uppercase extension still matches', fileName: 'CLIP.MP4', expected: 'video' },
    { name: 'case-insensitive: mixed-case extension still matches', fileName: 'song.Mp3', expected: 'audio' },
    {
      name: 'multi-dot name: only the last segment counts as the extension',
      fileName: 'my.video.file.mp4',
      expected: 'video',
    },
    { name: 'no extension at all returns null', fileName: 'README', expected: null },
    { name: 'a trailing dot with nothing after it returns null', fileName: 'weird.', expected: null },
    { name: 'unknown extension returns null', fileName: 'document.pdf', expected: null },
    { name: 'an unrelated media-ish extension (image) returns null', fileName: 'photo.png', expected: null },
    { name: 'empty string returns null', fileName: '', expected: null },
  ];

  for (const { name, fileName, expected } of cases) {
    it(name, () => {
      expect(mediaKind(fileName)).toBe(expected);
    });
  }
});

describe('streamUrl', () => {
  beforeEach(() => {
    Platform.OS = 'web';
  });

  afterEach(() => {
    Platform.OS = 'ios';
  });

  it('builds the exact /api/media/stream?path=<encoded> shape', () => {
    expect(streamUrl('videos/clip.mp4')).toBe('/api/media/stream?path=videos%2Fclip.mp4');
  });

  it('encodes a space in the path', () => {
    expect(streamUrl('my videos/clip.mp4')).toBe('/api/media/stream?path=my%20videos%2Fclip.mp4');
  });

  it('encodes a "#" in the path (fragment-significant char, must not reach the URL raw)', () => {
    expect(streamUrl('clip#1.mp4')).toBe('/api/media/stream?path=clip%231.mp4');
  });

  it('encodes a "?" in the path (query-significant char, must not reach the URL raw)', () => {
    expect(streamUrl('clip?final.mp4')).toBe('/api/media/stream?path=clip%3Ffinal.mp4');
  });

  it('encodes unicode characters in the path', () => {
    expect(streamUrl('видео/тест файл.mp4')).toBe(
      '/api/media/stream?path=%D0%B2%D0%B8%D0%B4%D0%B5%D0%BE%2F%D1%82%D0%B5%D1%81%D1%82%20%D1%84%D0%B0%D0%B9%D0%BB.mp4',
    );
  });

  it('applies real encodeURIComponent, not a pass-through (raw special chars never appear verbatim)', () => {
    const url = streamUrl('a b#c?d');
    expect(url).toBe(`/api/media/stream?path=${encodeURIComponent('a b#c?d')}`);
    expect(url).not.toContain(' ');
    expect(url).not.toContain('#c');
    expect(url).not.toContain('?d');
  });

  it('prefixes with apiBase() on native (non-web) instead of being always-relative', () => {
    Platform.OS = 'ios';
    expect(streamUrl('clip.mp4')).toBe('http://homeai.local/api/media/stream?path=clip.mp4');
  });
});
