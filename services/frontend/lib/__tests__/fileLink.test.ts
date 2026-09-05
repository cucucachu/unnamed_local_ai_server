import { filePathFromHref, normalizeFileLink } from '../fileLink';

describe('filePathFromHref', () => {
  it('accepts a bare root-relative href the model sometimes emits', () => {
    expect(filePathFromHref('notes/link-test.md')).toBe('notes/link-test.md');
  });

  it('ignores http(s) and mailto links', () => {
    expect(filePathFromHref('https://example.com/x.md')).toBeNull();
    expect(filePathFromHref('mailto:a@b.c')).toBeNull();
  });
});

describe('normalizeFileLink', () => {
  const cases: { href: string; expected: string }[] = [
    { href: 'file:notes/x.md', expected: 'notes/x.md' },
    { href: 'file:/files/notes/x.md', expected: 'notes/x.md' },
    { href: 'file:///files/notes/x.md', expected: 'notes/x.md' },
    { href: 'file://files/notes/x.md', expected: 'notes/x.md' },
    { href: 'file:/notes/x.md', expected: 'notes/x.md' },
    { href: 'file:notes/hello%20world.md', expected: 'notes/hello world.md' },
    { href: 'file:', expected: '' },
    { href: 'file:///files', expected: '' },
  ];

  it.each(cases)('normalizes $href to $expected', ({ href, expected }) => {
    expect(normalizeFileLink(href)).toBe(expected);
  });
});
