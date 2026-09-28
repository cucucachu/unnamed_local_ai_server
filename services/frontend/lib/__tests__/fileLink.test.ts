import { filePathFromHref, normalizeFileLink } from '../fileLink';

describe('filePathFromHref', () => {
  it('maps a bare relative href the model sometimes emits into the personal space', () => {
    expect(filePathFromHref('notes/link-test.md')).toBe('/personal/notes/link-test.md');
  });

  it('keeps space paths', () => {
    expect(filePathFromHref('/spaces/family/a.md')).toBe('/spaces/family/a.md');
    expect(filePathFromHref('/personal/a.md')).toBe('/personal/a.md');
  });

  it("maps the exec shell's /files/... spellings onto the same spaces", () => {
    expect(filePathFromHref('/files/personal/a.md')).toBe('/personal/a.md');
    expect(filePathFromHref('/files/spaces/family/a.md')).toBe('/spaces/family/a.md');
  });

  it('ignores http(s) and mailto links', () => {
    expect(filePathFromHref('https://example.com/x.md')).toBeNull();
    expect(filePathFromHref('mailto:a@b.c')).toBeNull();
  });
});

describe('normalizeFileLink', () => {
  const cases: { href: string; expected: string }[] = [
    { href: 'file:notes.txt', expected: '/personal/notes.txt' },
    { href: 'file:notes/x.md', expected: '/personal/notes/x.md' },
    { href: 'file:/files/notes/x.md', expected: '/personal/notes/x.md' },
    { href: 'file:///files/notes/x.md', expected: '/personal/notes/x.md' },
    { href: 'file://files/notes/x.md', expected: '/personal/notes/x.md' },
    { href: 'file:/notes/x.md', expected: '/personal/notes/x.md' },
    {
      href: 'file:notes/hello%20world.md',
      expected: '/personal/notes/hello world.md',
    },
    { href: 'file:', expected: '/personal' },
    { href: 'file:///files', expected: '/personal' },
    { href: 'file:/personal/notes/x.md', expected: '/personal/notes/x.md' },
    { href: 'file:personal', expected: '/personal' },
    { href: 'file:/spaces/family/trip/', expected: '/spaces/family/trip' },
    {
      href: 'file:///spaces/family/a%20b.md',
      expected: '/spaces/family/a b.md',
    },
    { href: 'file:/spaces', expected: '/spaces' },
    { href: 'file:personalnotes.md', expected: '/personal/personalnotes.md' },
    { href: 'file:/files/personal/notes/x.md', expected: '/personal/notes/x.md' },
    { href: 'file:///files/personal/x.md', expected: '/personal/x.md' },
    { href: 'file:/files/personal', expected: '/personal' },
    { href: 'file:/files/spaces/family/trip/a.md', expected: '/spaces/family/trip/a.md' },
    {
      href: 'file:///files/spaces/family/a%20b.md',
      expected: '/spaces/family/a b.md',
    },
    { href: 'file:/files/spaces/family/', expected: '/spaces/family' },
    { href: 'file:/files/spaces', expected: '/spaces' },
    { href: 'file:/files/personalnotes.md', expected: '/personal/personalnotes.md' },
    { href: 'file:/personal/files/x.md', expected: '/personal/files/x.md' },
  ];

  it.each(cases)('normalizes $href to $expected', ({ href, expected }) => {
    expect(normalizeFileLink(href)).toBe(expected);
  });
});
