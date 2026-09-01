import { categoryFor, formatFileSize, iconNameFor, type FileCategory } from '../fileDisplay';

describe('formatFileSize', () => {
  const cases: { name: string; bytes: number; expected: string }[] = [
    { name: 'zero bytes', bytes: 0, expected: '0 B' },
    { name: 'sub-KB stays in bytes', bytes: 512, expected: '512 B' },
    { name: 'just under the KB boundary stays in bytes', bytes: 1023, expected: '1023 B' },
    { name: 'exactly 1 KB drops the ".0"', bytes: 1024, expected: '1 KB' },
    { name: 'a fractional KB keeps one decimal', bytes: 1536, expected: '1.5 KB' },
    { name: 'exactly 1 MB drops the ".0"', bytes: 1024 * 1024, expected: '1 MB' },
    { name: 'a fractional MB keeps one decimal', bytes: 1024 * 1024 * 2.5, expected: '2.5 MB' },
    { name: 'exactly 1 GB drops the ".0"', bytes: 1024 * 1024 * 1024, expected: '1 GB' },
    { name: 'exactly 1 TB drops the ".0"', bytes: 1024 * 1024 * 1024 * 1024, expected: '1 TB' },
    { name: 'beyond 1 TB stays in TB rather than growing to PB', bytes: 1024 * 1024 * 1024 * 1024 * 2, expected: '2 TB' },
    { name: 'a negative size (invalid) returns an empty string', bytes: -5, expected: '' },
    { name: 'NaN (invalid) returns an empty string', bytes: NaN, expected: '' },
  ];

  for (const { name, bytes, expected } of cases) {
    it(name, () => {
      expect(formatFileSize(bytes)).toBe(expected);
    });
  }
});

describe('categoryFor / iconNameFor', () => {
  const cases: { name: string; entry: { type: 'file' | 'dir'; mime: string | null }; expectedCategory: FileCategory }[] = [
    { name: 'a directory is always "folder", regardless of mime', entry: { type: 'dir', mime: null }, expectedCategory: 'folder' },
    {
      name: 'a directory is "folder" even if it somehow carried an image mime',
      entry: { type: 'dir', mime: 'image/png' },
      expectedCategory: 'folder',
    },
    { name: 'image/* maps to "image"', entry: { type: 'file', mime: 'image/png' }, expectedCategory: 'image' },
    { name: 'video/* maps to "video"', entry: { type: 'file', mime: 'video/mp4' }, expectedCategory: 'video' },
    { name: 'audio/* maps to "audio"', entry: { type: 'file', mime: 'audio/mpeg' }, expectedCategory: 'audio' },
    { name: 'text/* maps to "text"', entry: { type: 'file', mime: 'text/plain' }, expectedCategory: 'text' },
    { name: 'a known archive mime maps to "archive"', entry: { type: 'file', mime: 'application/zip' }, expectedCategory: 'archive' },
    {
      name: 'an unrecognized mime falls back to "other"',
      entry: { type: 'file', mime: 'application/pdf' },
      expectedCategory: 'other',
    },
    { name: 'a null mime (extension-less/unknown file) falls back to "other"', entry: { type: 'file', mime: null }, expectedCategory: 'other' },
  ];

  const EXPECTED_ICON: Record<FileCategory, string> = {
    folder: 'folder',
    image: 'image-outline',
    video: 'videocam-outline',
    audio: 'musical-notes-outline',
    text: 'document-text-outline',
    archive: 'archive-outline',
    other: 'document-outline',
  };

  for (const { name, entry, expectedCategory } of cases) {
    it(name, () => {
      expect(categoryFor(entry)).toBe(expectedCategory);
      expect(iconNameFor(entry)).toBe(EXPECTED_ICON[expectedCategory]);
    });
  }
});
